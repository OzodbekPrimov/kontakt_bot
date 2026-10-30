from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from app.domain.models import DeliveryStatus, Payload


class RetryAfterMiddleware(BaseRequestMiddleware):
    """Retry only explicit refusals. Network timeouts may already have delivered the message."""

    async def __call__(self, make_request, bot, method):
        for attempt in range(4):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                if attempt == 3:
                    raise
                await asyncio.sleep(max(0, exc.retry_after) + 0.1)
        raise RuntimeError("Unreachable retry state")


class TelegramGateway:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_lookup(self, user_id: int, html: str) -> DeliveryStatus:
        try:
            await self.bot.send_message(user_id, html, parse_mode="HTML")
        except TelegramForbiddenError:
            return DeliveryStatus.BLOCKED
        except TelegramNetworkError:
            return DeliveryStatus.UNKNOWN
        except TelegramAPIError:
            return DeliveryStatus.FAILED
        return DeliveryStatus.SENT

    async def send(self, user_id: int, payload: Payload) -> DeliveryStatus:
        sent_part = False
        try:
            text = payload.text
            if payload.photo_id:
                await self.bot.send_photo(
                    user_id, payload.photo_id, caption=text[:1024] or None, parse_mode=None
                )
                sent_part = True
                text = text[1024:]
            for offset in range(0, len(text), 4096):
                await self.bot.send_message(user_id, text[offset : offset + 4096], parse_mode=None)
                sent_part = True
        except TelegramForbiddenError:
            return DeliveryStatus.BLOCKED
        except TelegramNetworkError:
            return DeliveryStatus.UNKNOWN
        except TelegramAPIError:
            return DeliveryStatus.UNKNOWN if sent_part else DeliveryStatus.FAILED
        return DeliveryStatus.SENT
