from __future__ import annotations

import asyncio
import logging

from app.application.ports import MessageGateway, Storage
from app.domain.models import Campaign, DeliveryStatus, Payload

logger = logging.getLogger(__name__)


class MessagingService:
    """On-demand delivery inside the bot process; no separate queue or worker."""

    def __init__(self, storage: Storage, gateway: MessageGateway, admin_ids: set[int]) -> None:
        self.storage = storage
        self.gateway = gateway
        self.admin_ids = admin_ids
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def require_admin(self, user_id: int) -> None:
        if user_id not in self.admin_ids:
            raise PermissionError("Admin access required")

    async def create(
        self,
        admin_id: int,
        payload: Payload,
        target_id: int | None,
        recipient_ids: list[int] | None = None,
    ) -> Campaign:
        self.require_admin(admin_id)
        if target_id is not None and await self.storage.get_user(target_id) is None:
            raise ValueError("Recipient has not used the bot")
        return await self.storage.create_campaign(admin_id, payload, target_id, recipient_ids)

    async def start(self, admin_id: int, campaign_id: int) -> bool:
        self.require_admin(admin_id)
        if campaign_id in self._tasks and not self._tasks[campaign_id].done():
            return False
        if not await self.storage.claim_campaign(campaign_id):
            return False
        self._tasks[campaign_id] = asyncio.create_task(self._run(campaign_id))
        return True

    async def pause(self, admin_id: int, campaign_id: int) -> None:
        self.require_admin(admin_id)
        await self.storage.pause_campaign(campaign_id)

    async def _run(self, campaign_id: int) -> None:
        recipient = None
        try:
            campaign = await self.storage.get_campaign(campaign_id)
            if campaign is None:
                return
            while (recipient := await self.storage.next_recipient(campaign_id)) is not None:
                status = await self.gateway.send(recipient, campaign.payload)
                await self.storage.complete_delivery(campaign_id, recipient, status.value)
                if status == DeliveryStatus.BLOCKED:
                    await self.storage.set_blocked(recipient, True)
                recipient = None
                # Sending pace is Telegram API compliance, not a user request quota.
                await asyncio.sleep(0.05)
            await self.storage.finish_campaign(campaign_id)
            latest = await self.storage.get_campaign(campaign_id)
            counts = await self.storage.campaign_counts(campaign_id)
            await self.gateway.send(
                campaign.admin_id,
                Payload(
                    f"Yuborish #{campaign_id}: {latest.status if latest else 'unknown'}\n"
                    f"Yuborildi: {counts.get('sent', 0)}\n"
                    f"Qolgan: {counts.get('pending', 0)}\n"
                    f"Bloklangan: {counts.get('blocked', 0)}\n"
                    f"Xato: {counts.get('failed', 0)}\n"
                    f"Yetkazilishi noma’lum: {counts.get('unknown', 0)}"
                ),
            )
        except asyncio.CancelledError:
            if recipient is not None:
                await self.storage.complete_delivery(campaign_id, recipient, "unknown")
            await self.storage.pause_campaign(campaign_id)
            raise
        except Exception as exc:
            # Do not log payloads, phone numbers, tokens, or Telegram request bodies.
            logger.error("Campaign %s interrupted: %s", campaign_id, type(exc).__name__)
            if recipient is not None:
                await self.storage.complete_delivery(campaign_id, recipient, "unknown")
            await self.storage.pause_campaign(campaign_id)
        finally:
            self._tasks.pop(campaign_id, None)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
