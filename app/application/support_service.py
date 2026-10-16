from __future__ import annotations

from app.application.ports import MessageGateway, Storage
from app.domain.models import DeliveryStatus, Payload, Ticket


class SupportService:
    def __init__(self, storage: Storage, gateway: MessageGateway, admin_ids: set[int]) -> None:
        self.storage = storage
        self.gateway = gateway
        self.admin_ids = admin_ids

    def require_admin(self, user_id: int) -> None:
        if user_id not in self.admin_ids:
            raise PermissionError("Admin access required")

    async def submit(
        self, user_id: int, chat_id: int, message_id: int, payload: Payload
    ) -> tuple[Ticket, bool]:
        ticket, created = await self.storage.add_ticket_message(
            user_id, chat_id, message_id, payload
        )
        return ticket, created

    async def reply(
        self, admin_id: int, ticket_id: int, chat_id: int, message_id: int, payload: Payload
    ) -> DeliveryStatus | None:
        self.require_admin(admin_id)
        ticket = await self.storage.get_ticket(ticket_id)
        if ticket is None or ticket.status == "closed":
            return None
        reply_id = await self.storage.reserve_reply(
            ticket_id, admin_id, chat_id, message_id, payload
        )
        if reply_id is None:
            return None
        outgoing = Payload(
            f"Admin javobi · Murojaat #{ticket_id}\n\n{payload.text}", payload.photo_id
        )
        status = await self.gateway.send(ticket.user_id, outgoing)
        await self.storage.finish_reply(reply_id, status.value)
        if status == DeliveryStatus.BLOCKED:
            await self.storage.set_blocked(ticket.user_id, True)
        return status
