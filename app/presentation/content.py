from aiogram.types import Message

from app.domain.models import Payload, UserProfile


def payload_from(message: Message) -> Payload | None:
    if message.photo:
        return Payload(text=message.caption or "", photo_id=message.photo[-1].file_id)
    if message.text and not message.text.startswith("/"):
        return Payload(text=message.text)
    return None


def sender_profile(message: Message) -> UserProfile:
    user = message.from_user
    if user is None:
        raise ValueError("User is required")
    return UserProfile(user.id, user.first_name, user.last_name, user.username)
