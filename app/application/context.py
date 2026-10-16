from dataclasses import dataclass

from app.application.messaging_service import MessagingService
from app.application.ports import MessageGateway, Storage
from app.application.services import PhoneLookupService
from app.application.support_service import SupportService


@dataclass(slots=True)
class AppContext:
    storage: Storage
    lookup: PhoneLookupService
    support: SupportService
    messaging: MessagingService
    gateway: MessageGateway
    admin_ids: set[int]

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids
