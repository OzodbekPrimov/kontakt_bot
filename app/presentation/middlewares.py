from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.application.ports import Storage
from app.domain.models import UserProfile


class ActivityMiddleware(BaseMiddleware):
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user, chat = event.from_user, event.chat
        elif isinstance(event, CallbackQuery):
            user, chat = event.from_user, event.message.chat if event.message else None
        else:
            return await handler(event, data)
        if chat is None or chat.type != "private" or user is None or user.is_bot:
            if isinstance(event, CallbackQuery):
                await event.answer("Botni shaxsiy chatda oching.", show_alert=True)
            return None
        await self.storage.touch_user(
            UserProfile(
                telegram_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
            )
        )
        return await handler(event, data)
