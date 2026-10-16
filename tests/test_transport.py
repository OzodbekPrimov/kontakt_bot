import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.domain.models import DeliveryStatus, Payload
from app.infrastructure.telegram import RetryAfterMiddleware, TelegramGateway


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_429_waits_and_retries_the_same_request(self):
        method = SendMessage(chat_id=1, text="Hello")
        request = AsyncMock(side_effect=[TelegramRetryAfter(method, "Slow down", 2), True])
        with patch("app.infrastructure.telegram.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await RetryAfterMiddleware()(request, None, method)
        self.assertTrue(result)
        self.assertEqual(request.await_count, 2)
        sleep.assert_awaited_once_with(2.1)

    async def test_network_timeout_is_not_retried(self):
        method = SendMessage(chat_id=1, text="Hello")
        request = AsyncMock(side_effect=TelegramNetworkError(method, "Timeout"))
        with self.assertRaises(TelegramNetworkError):
            await RetryAfterMiddleware()(request, None, method)
        self.assertEqual(request.await_count, 1)

    async def test_forbidden_and_uncertain_delivery_are_distinguished(self):
        method = SendMessage(chat_id=1, text="Hello")
        bot = AsyncMock(spec=Bot)
        bot.send_message.side_effect = TelegramForbiddenError(method, "Blocked")
        gateway = TelegramGateway(bot)
        self.assertEqual(await gateway.send(1, Payload("Hello")), DeliveryStatus.BLOCKED)
        bot.send_message.side_effect = TelegramNetworkError(method, "Timeout")
        self.assertEqual(await gateway.send(1, Payload("Hello")), DeliveryStatus.UNKNOWN)

    async def test_long_photo_caption_is_delivered_without_truncation(self):
        bot = AsyncMock(spec=Bot)
        text = "x" * 5000
        self.assertEqual(
            await TelegramGateway(bot).send(1, Payload(text, "photo-file-id")), DeliveryStatus.SENT
        )
        self.assertEqual(bot.send_photo.await_args.kwargs["caption"], text[:1024])
        self.assertEqual(bot.send_message.await_args.args[1], text[1024:])
