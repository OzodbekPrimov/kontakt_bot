from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import ErrorEvent

from app.application.context import AppContext
from app.application.lookup_worker import LookupWorker
from app.application.messaging_service import MessagingService
from app.application.services import PhoneLookupService
from app.application.support_service import SupportService
from app.config import Settings
from app.domain.models import Payload
from app.infrastructure.database import Database
from app.infrastructure.telegram import RetryAfterMiddleware, TelegramGateway
from app.infrastructure.user_lookup import UserLookupClient
from app.presentation.middlewares import ActivityMiddleware
from app.presentation.routes import create_router


async def on_error(event: ErrorEvent, context: AppContext) -> bool:
    # Traceback/request reprs may contain user inputs or credentials.
    logging.getLogger(__name__).error(
        "Update %s failed: %s", event.update.update_id, type(event.exception).__name__
    )
    message = event.update.message
    callback = event.update.callback_query
    recipient = (
        message.chat.id
        if message and message.chat.type == "private"
        else (callback.from_user.id if callback else None)
    )
    if recipient:
        await context.gateway.send(
            recipient,
            Payload(
                "Amalni yakunlab bo‘lmadi. /cancel bilan menyuga qayting va qayta urinib ko‘ring."
            ),
        )
    return True


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    database = await Database.open(Path("data/bot.sqlite3"))
    bot = None
    messaging = None
    lookup_worker = None
    lookup_client = None
    try:
        bot = Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        bot.session.middleware(RetryAfterMiddleware())
        admins = set(settings.admin_ids)
        gateway = TelegramGateway(bot)
        messaging = MessagingService(database, gateway, admins)
        if settings.lookup_enabled:
            session = (
                settings.lookup_session.get_secret_value()
                if settings.lookup_session
                else settings.lookup_session_file.read_text(encoding="utf-8").strip()
            )
            lookup_client = UserLookupClient(
                settings.lookup_api_id,
                settings.lookup_api_hash.get_secret_value(),
                session,
            )
            await lookup_client.connect()
            lookup_worker = LookupWorker(
                database,
                gateway,
                lookup_client,
                settings.lookup_free_limit,
                settings.lookup_open_access,
            )
            lookup_worker.start()
        context = AppContext(
            database,
            PhoneLookupService(),
            SupportService(database, gateway, admins),
            messaging,
            gateway,
            admins,
            lookup_worker,
        )
        dispatcher = Dispatcher(context=context, events_isolation=SimpleEventIsolation())
        activity = ActivityMiddleware(database)
        dispatcher.message.outer_middleware(activity)
        dispatcher.callback_query.outer_middleware(activity)
        dispatcher.errors.register(on_error)
        dispatcher.include_router(create_router())
        if not admins:
            logging.warning("ADMIN_IDS is empty; admin features are disabled")
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        if lookup_worker:
            await lookup_worker.close()
        if lookup_client:
            await lookup_client.close()
        if messaging:
            await messaging.close()
        if bot:
            await bot.session.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
