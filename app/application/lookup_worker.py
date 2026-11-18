from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Protocol

from app.domain.models import DeliveryStatus, Payload
from app.domain.phone_number import PhoneNumber
from app.domain.telegram_links import profile_link_for
from app.infrastructure.user_lookup import LookupFloodWait, ResolvedAccount

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Consecutive technical failures after which the lookup session is probably broken.
HEALTH_ALERT_THRESHOLD = 3


class Resolver(Protocol):
    async def resolve(self, phone: str) -> ResolvedAccount | None: ...


def found_text(masked_phone: str, phone: str, account: ResolvedAccount) -> str:
    name = " ".join(filter(None, (account.first_name, account.last_name)))
    username = f"@{escape(account.username)}" if account.username else "O‘rnatilmagan"
    link = (
        f"https://t.me/{account.username}"
        if account.username
        else profile_link_for(PhoneNumber(phone))
    )
    return (
        f"<b>Telegram profili aniqlandi</b>\n"
        f"Raqam: {escape(masked_phone)}\n"
        f"Ism: {escape(name) or 'Ko‘rsatilmagan'}\n"
        f"ID: <code>{account.user_id}</code>\n"
        f"Username: {username}\n"
        f'<a href="{link}">Profilni ochish</a>\n\n'
        "Profilni sizning akkauntingizda ochish Telegram maxfiylik sozlamalariga bog‘liq."
    )


def not_visible_text(masked_phone: str) -> str:
    return (
        f"{escape(masked_phone)}: raqam orqali Telegram profili aniqlanmadi. "
        "Bu natija akkaunt yo‘qligini tasdiqlamaydi: akkaunt mavjud bo‘lmasligi yoki "
        "egasi maxfiylik sozlamasida raqam bo‘yicha topishni yopgan bo‘lishi mumkin. "
        "Limit ayrilmaydi."
    )


TECHNICAL_ERROR_TEXT = "Tekshiruvni yakunlab bo‘lmadi. Limit ayrilmadi."


class LookupWorker:
    """Drains the persisted lookup queue on one user session, within Telegram's limits."""

    def __init__(
        self,
        storage,
        gateway,
        resolver: Resolver,
        free_limit: int,
        open_access: bool = False,
        *,
        max_active_per_user: int | None = None,
        admin_ids: set[int] | frozenset[int] = frozenset(),
    ) -> None:
        self.storage = storage
        self.gateway = gateway
        self.resolver = resolver
        self.free_limit = free_limit
        self.open_access = open_access
        self.max_active_per_user = max_active_per_user
        self.admin_ids = admin_ids
        self._failures = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="lookup-worker")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run(self) -> None:
        while True:
            try:
                if await self.process_one():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Lookup worker error: %s", type(exc).__name__)
            await asyncio.sleep(1)

    async def process_one(self) -> bool:
        """Deliver one finished result, else resolve one queued number. False when idle."""
        return await self._deliver_one() or await self._resolve_one()

    async def _deliver_one(self) -> bool:
        delivery = await self.storage.claim_lookup_delivery()
        if delivery is None:
            return False
        try:
            status = await self.gateway.send_lookup(delivery["chat_id"], delivery["result_text"])
        except Exception as exc:
            logger.error("Lookup delivery error: %s", type(exc).__name__)
            status = DeliveryStatus.UNKNOWN
        await self.storage.finish_lookup_delivery(delivery["id"], status.value)
        if status == DeliveryStatus.BLOCKED:
            await self.storage.set_blocked(delivery["user_id"], True)
        return True

    async def _resolve_one(self) -> bool:
        request = await self.storage.claim_lookup()
        if request is None:
            return False
        try:
            account = await self.resolver.resolve(request["phone"])
        except LookupFloodWait as exc:
            # Telegram's own limit: wait it out. Hammering here is what gets accounts banned.
            logger.warning("Telegram lookup rate limited; waiting %s seconds", exc.seconds)
            await self.storage.defer_lookup(request["id"], exc.seconds)
            return True
        except Exception as exc:
            logger.error("Lookup resolution error: %s", type(exc).__name__)
            await self._record_failure()
            if request["attempts"] >= MAX_ATTEMPTS:
                await self.storage.complete_lookup_resolution(
                    request["id"], False, TECHNICAL_ERROR_TEXT, "technical_error"
                )
            else:
                await self.storage.defer_lookup(
                    request["id"], min(300, 10 * 2 ** request["attempts"])
                )
            return True
        self._failures = 0
        if account is None:
            await self.storage.complete_lookup_resolution(
                request["id"], False, not_visible_text(request["masked_phone"]), "not_visible"
            )
        else:
            await self.storage.complete_lookup_resolution(
                request["id"],
                True,
                found_text(request["masked_phone"], request["phone"], account),
                "found",
            )
        return True

    async def _record_failure(self) -> None:
        self._failures += 1
        if self._failures != HEALTH_ALERT_THRESHOLD:
            return
        alert = Payload(
            f"⚠️ Server qidiruvi ketma-ket {HEALTH_ALERT_THRESHOLD} marta xato berdi. "
            "Lookup sessiyasi yoki tarmoqni tekshiring (journalctl / docker logs)."
        )
        for admin_id in self.admin_ids:
            try:
                await self.gateway.send(admin_id, alert)
            except Exception as exc:
                logger.error("Lookup health alert failed: %s", type(exc).__name__)
