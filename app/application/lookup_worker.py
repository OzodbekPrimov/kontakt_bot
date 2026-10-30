from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Protocol

from app.domain.models import DeliveryStatus
from app.domain.phone_number import PhoneNumber
from app.domain.telegram_links import profile_link_for
from app.infrastructure.user_lookup import LookupFloodWait, ResolvedAccount

logger = logging.getLogger(__name__)


class Resolver(Protocol):
    async def resolve(self, phone: str) -> ResolvedAccount | None: ...


class LookupWorker:
    def __init__(
        self,
        storage,
        gateway,
        resolver: Resolver,
        free_limit: int,
        open_access: bool = False,
    ) -> None:
        self.storage = storage
        self.gateway = gateway
        self.resolver = resolver
        self.free_limit = free_limit
        self.open_access = open_access
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
        delivery = await self.storage.claim_lookup_delivery()
        if delivery is not None:
            try:
                status = await self.gateway.send_lookup(
                    delivery["chat_id"], delivery["result_text"]
                )
            except Exception as exc:
                logger.error("Lookup delivery error: %s", type(exc).__name__)
                status = DeliveryStatus.UNKNOWN
            await self.storage.finish_lookup_delivery(delivery["id"], status.value)
            return True
        request = await self.storage.claim_lookup()
        if request is None:
            return False
        try:
            account = await self.resolver.resolve(request["phone"])
        except LookupFloodWait as exc:
            await self.storage.defer_lookup(request["id"], exc.seconds)
            return True
        except Exception as exc:
            logger.error("Lookup resolution error: %s", type(exc).__name__)
            if request["attempts"] >= 3:
                await self.storage.complete_lookup_resolution(
                    request["id"],
                    False,
                    "Tekshiruvni yakunlab bo‘lmadi. Limit ayrilmadi.",
                )
            else:
                await self.storage.defer_lookup(
                    request["id"], min(300, 10 * 2 ** request["attempts"])
                )
            return True
        if account is None:
            await self.storage.complete_lookup_resolution(
                request["id"],
                False,
                f"{escape(request['masked_phone'])}: raqam orqali Telegram profili aniqlanmadi. "
                "Akkaunt yo‘q yoki uning maxfiylik sozlamasi qidiruvga ruxsat bermaydi. "
                "Limit ayrilmaydi.",
            )
        else:
            name = " ".join(filter(None, (account.first_name, account.last_name)))
            username = f"@{escape(account.username)}" if account.username else "O‘rnatilmagan"
            link = (
                f"https://t.me/{account.username}"
                if account.username
                else profile_link_for(PhoneNumber(request["phone"]))
            )
            await self.storage.complete_lookup_resolution(
                request["id"],
                True,
                f"<b>Telegram profili aniqlandi</b>\n"
                f"Raqam: {escape(request['masked_phone'])}\n"
                f"Ism: {escape(name) or 'Ko‘rsatilmagan'}\n"
                f"ID: <code>{account.user_id}</code>\n"
                f"Username: {username}\n"
                f'<a href="{link}">Profilni ochish</a>\n\n'
                "Profilni sizning akkauntingizda ochish Telegram maxfiylik sozlamalariga bog‘liq.",
            )
        return True
