import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.methods import AnswerCallbackQuery, GetMe, SendMessage, SendPhoto
from aiogram.types import Chat, Message, ReplyKeyboardMarkup, Update, User

from app.application.context import AppContext
from app.application.messaging_service import MessagingService
from app.application.services import PhoneLookupService
from app.application.support_service import SupportService
from app.infrastructure.database import Database
from app.infrastructure.telegram import TelegramGateway
from app.main import on_error
from app.presentation import keyboards as kb
from app.presentation.middlewares import ActivityMiddleware
from app.presentation.routes import create_router
from app.presentation.states import AdminFlow


class OfflineSession(BaseSession):
    """A real Dispatcher with a fake Telegram transport: no token/network needed."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Test Bot", username="test_bot")
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(
                message_id=10000 + len(self.calls),
                date=1700000000,
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=User(id=bot.id, is_bot=True, first_name="Test Bot"),
                text=method.text if isinstance(method, SendMessage) else None,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = await Database.open(Path(self.directory.name) / "bot.sqlite3")
        self.session = OfflineSession()
        self.bot = Bot(
            "999999:" + "x" * 35,
            session=self.session,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        gateway = TelegramGateway(self.bot)
        self.messaging = MessagingService(self.db, gateway, {99})
        self.context = AppContext(
            self.db,
            PhoneLookupService(),
            SupportService(self.db, gateway, {99}),
            self.messaging,
            gateway,
            {99},
        )
        self.dispatcher = Dispatcher(context=self.context, events_isolation=SimpleEventIsolation())
        activity = ActivityMiddleware(self.db)
        self.dispatcher.message.outer_middleware(activity)
        self.dispatcher.callback_query.outer_middleware(activity)
        self.dispatcher.include_router(create_router())
        self.counter = 0

    async def asyncTearDown(self):
        await self.messaging.close()
        await self.dispatcher.storage.close()
        await self.bot.session.close()
        await self.db.close()
        self.directory.cleanup()

    async def feed(self, user_id, text=None, *, message_id=None, **payload):
        self.counter += 1
        sender = {
            "id": user_id,
            "is_bot": False,
            "first_name": f"User {user_id}",
            "username": f"user{user_id}",
        }
        message = {
            "message_id": message_id or self.counter,
            "date": 1700000000,
            "chat": {"id": user_id, "type": "private"},
            "from": sender,
            **payload,
        }
        if text is not None:
            message["text"] = text
        await self.dispatcher.feed_update(
            self.bot,
            Update.model_validate(
                {
                    "update_id": self.counter,
                    "message": message,
                }
            ),
        )

    async def callback(self, user_id, data):
        self.counter += 1
        await self.dispatcher.feed_update(
            self.bot,
            Update.model_validate(
                {
                    "update_id": self.counter,
                    "callback_query": {
                        "id": str(self.counter),
                        "chat_instance": "private-test",
                        "data": data,
                        "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                        "message": {
                            "message_id": self.counter,
                            "date": 1700000000,
                            "chat": {"id": user_id, "type": "private"},
                            "from": {"id": self.bot.id, "is_bot": True, "first_name": "Bot"},
                        },
                    },
                }
            ),
        )

    def messages(self, user_id):
        return [
            call
            for call in self.session.calls
            if isinstance(call, SendMessage) and call.chat_id == user_id
        ]

    def state(self, user_id):
        return self.dispatcher.fsm.get_context(bot=self.bot, chat_id=user_id, user_id=user_id)

    async def test_persistent_bottom_menu_and_admin_visibility(self):
        await self.feed(1, "/start")
        menu = self.messages(1)[-1].reply_markup
        self.assertIsInstance(menu, ReplyKeyboardMarkup)
        self.assertTrue(menu.resize_keyboard)
        self.assertTrue(menu.is_persistent)
        buttons = [button for row in menu.keyboard for button in row]
        self.assertNotIn(kb.ADMIN, [button.text for button in buttons])
        selector = next(button for button in buttons if button.text == kb.OTHER_ID)
        self.assertTrue(selector.request_users.request_username)
        await self.feed(99, "/start")
        self.assertIn(
            kb.ADMIN,
            [button.text for row in self.messages(99)[-1].reply_markup.keyboard for button in row],
        )

    async def test_my_id_and_username_belong_to_the_sender(self):
        await self.feed(1, kb.MY_ID)
        card = self.messages(1)[-1].text
        self.assertIn("Telegram ID: <code>1</code>", card)
        self.assertIn("@user1", card)

    async def test_phone_text_does_not_claim_the_senders_id_as_the_phone_owner(self):
        await self.feed(1, "+998901234567")
        text = self.messages(1)[-1].text
        self.assertIn("Telegram ID: Berilmagan", text)
        self.assertNotIn("@user1", text)
        self.assertIn("O‘zbekiston", text)
        self.assertIn("Mobil", text)
        self.assertIn("Dastlabki operator", text)
        self.assertEqual((await self.db.user_stats(1)).phone_requests, 1)

    async def test_server_lookup_queues_without_claiming_a_found_profile(self):
        self.context.lookup_worker = SimpleNamespace(free_limit=5, open_access=True)
        await self.feed(1, "+998901234567")
        self.assertIn("navbatga olindi", self.messages(1)[-1].text)
        self.assertNotIn("Telegram profili aniqlandi", self.messages(1)[-1].text)
        rows = await self.db._rows("SELECT status FROM lookup_requests")
        self.assertEqual(rows, [{"status": "pending"}])
        self.assertIsNone(await self.db.lookup_balance(1))

    async def test_lookup_status_is_admin_only(self):
        self.context.lookup_worker = SimpleNamespace(free_limit=5, open_access=True)
        await self.feed(1, "/lookup_status")
        self.assertIn("faqat admin", self.messages(1)[-1].text)
        await self.feed(99, "/lookup_status")
        self.assertIn("Qidiruv holati", self.messages(99)[-1].text)

    async def test_foreign_contact_id_is_used_without_inventing_a_username(self):
        await self.feed(
            1,
            contact={
                "phone_number": "+998901234567",
                "first_name": "<b>Someone</b>",
                "user_id": 777,
            },
        )
        text = self.messages(1)[-1].text
        self.assertIn("<code>777</code>", text)
        self.assertNotIn("@user1", text)
        self.assertIn("&lt;b&gt;Someone&lt;/b&gt;", text)
        self.assertIsNone(await self.db.get_user(777))

    async def test_own_shared_contact_can_show_the_matching_username(self):
        await self.feed(
            1, contact={"phone_number": "+998901234567", "first_name": "Me", "user_id": 1}
        )
        self.assertIn("Username: @user1", self.messages(1)[-1].text)

    async def test_shared_user_is_not_registered_as_a_bot_participant(self):
        await self.feed(
            1,
            users_shared={
                "request_id": kb.USER_REQUEST_ID,
                "users": [{"user_id": 777, "first_name": "Selected", "username": "selected_user"}],
            },
        )
        text = self.messages(1)[-1].text
        self.assertIn("<code>777</code>", text)
        self.assertIn("@selected_user", text)
        self.assertIsNone(await self.db.get_user(777))
        self.assertEqual((await self.db.global_stats()).total, 1)
        self.assertEqual((await self.db.user_stats(1)).user_selections, 1)

    async def test_unknown_selection_request_is_rejected(self):
        await self.feed(1, users_shared={"request_id": 5, "users": [{"user_id": 777}]})
        self.assertEqual((await self.db.user_stats(1)).user_selections, 0)

    async def test_duplicate_contact_update_does_not_increment_statistics_twice(self):
        contact = {"phone_number": "+998901234567", "first_name": "Test"}
        await self.feed(1, contact=contact, message_id=333)
        await self.feed(1, contact=contact, message_id=333)
        self.assertEqual((await self.db.user_stats(1)).contacts, 1)

    async def test_menu_navigation_cancels_support_instead_of_forwarding_menu_text(self):
        await self.feed(1, kb.SUPPORT)
        await self.feed(1, kb.MY_ID)
        self.assertIsNone(await self.state(1).get_state())
        self.assertEqual((await self.db.list_tickets(0))[1], 0)
        self.assertEqual(self.messages(99), [])

    async def test_non_admin_cannot_open_tickets_or_forge_admin_state(self):
        await self.feed(1, kb.SUPPORT)
        await self.feed(1, "Private question")
        before = len(self.messages(2))
        await self.callback(2, "a:ticket:1")
        self.assertEqual(len(self.messages(2)), before)
        self.assertTrue(
            any(
                isinstance(call, AnswerCallbackQuery) and call.show_alert
                for call in self.session.calls
            )
        )
        state = self.state(2)
        await state.set_state(AdminFlow.compose)
        await state.update_data(mode="campaign", target_id=1)
        await self.feed(2, "Forged send")
        self.assertIsNone(await state.get_state())
        self.assertEqual(await self.db.list_campaigns(), [])

    async def test_support_reply_has_preview_and_sends_only_once_to_the_ticket_owner(self):
        await self.feed(1, kb.SUPPORT)
        await self.feed(1, "Question <b>plain text</b>")
        self.assertIn("Murojaat #1", self.messages(99)[-1].text)
        await self.callback(99, "a:ticket:1")
        rendered = next(m for m in self.messages(99) if "Question <b>plain text</b>" in m.text)
        self.assertIsNone(rendered.parse_mode)
        await self.callback(99, "a:reply:1")
        await self.feed(99, "Admin answer")
        self.assertFalse(any("Admin answer" in m.text for m in self.messages(1)))
        nonce = (await self.state(99).get_data())["nonce"]
        await self.callback(99, f"a:send:{nonce}")
        await self.callback(99, f"a:send:{nonce}")
        self.assertEqual(sum("Admin answer" in m.text for m in self.messages(1)), 1)
        self.assertEqual((await self.db.get_ticket(1)).status, "answered")

    async def test_stale_confirmation_cannot_send_a_new_draft(self):
        await self.feed(1, "/start")
        await self.callback(99, "a:target:1")
        await self.feed(99, "First draft")
        old_nonce = (await self.state(99).get_data())["nonce"]
        await self.feed(99, kb.HOME)
        await self.callback(99, "a:target:1")
        await self.feed(99, "Second draft")
        await self.callback(99, f"a:send:{old_nonce}")
        self.assertEqual(await self.db.list_campaigns(), [])
        self.assertEqual(await self.state(99).get_state(), AdminFlow.confirm.state)

    async def test_admin_direct_message_is_confirmed_and_not_broadcast_to_others(self):
        await self.feed(1, "/start")
        await self.feed(2, "/start")
        await self.callback(99, "a:target:1")
        await self.feed(99, "Only to user one")
        self.assertFalse(any("Only to user one" in m.text for m in self.messages(1)))
        nonce = (await self.state(99).get_data())["nonce"]
        await self.callback(99, f"a:send:{nonce}")
        await asyncio.gather(*list(self.messaging._tasks.values()))
        self.assertEqual(sum(m.text == "Only to user one" for m in self.messages(1)), 1)
        self.assertFalse(any("Only to user one" in m.text for m in self.messages(2)))

    async def test_selecting_an_admin_does_not_grant_the_sender_admin_access(self):
        await self.feed(
            1,
            users_shared={
                "request_id": kb.USER_REQUEST_ID,
                "users": [{"user_id": 99, "username": "admin"}],
            },
        )
        await self.feed(1, "/admin")
        self.assertIn("faqat admin", self.messages(1)[-1].text)

    async def test_callback_navigation_and_statistics_pages_are_usable(self):
        for user_id in range(1, 12):
            await self.feed(user_id, "/start")
        await self.feed(99, kb.ADMIN)
        await self.feed(99, kb.STATS)
        self.assertIn("Jami foydalanuvchilar", self.messages(99)[-1].text)
        await self.feed(99, kb.USERS)
        self.assertTrue(
            any(
                button.callback_data == "a:users:1"
                for row in self.messages(99)[-1].reply_markup.inline_keyboard
                for button in row
            )
        )
        await self.callback(99, "a:users:1")
        await self.callback(99, "a:search:0")
        await self.feed(99, "1")
        self.assertIn("<code>1</code>", self.messages(99)[-1].text)

    async def test_unexpected_error_logging_does_not_expose_phone_or_exception_body(self):
        self.dispatcher.errors.register(on_error)

        async def fail(*args, **kwargs):
            raise RuntimeError("sensitive-number-or-token")

        self.db.record_usage = fail
        with self.assertLogs("app.main", level="ERROR") as logs:
            await self.feed(1, "+998901234567")
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertNotIn("sensitive-number-or-token", " ".join(logs.output))
        self.assertNotIn("998901234567", " ".join(logs.output))
        self.assertIn("Amalni yakunlab", self.messages(1)[-1].text)

    async def test_back_from_an_admin_draft_restores_the_admin_keyboard(self):
        await self.feed(1, "/start")
        await self.callback(99, "a:target:1")
        await self.feed(99, kb.BACK)
        menu = self.messages(99)[-1].reply_markup
        self.assertIsInstance(menu, ReplyKeyboardMarkup)
        self.assertIn(kb.STATS, [button.text for row in menu.keyboard for button in row])
        self.assertIsNone(await self.state(99).get_state())

    async def test_admin_profile_links_to_stable_id_not_a_cached_username(self):
        await self.feed(1, "/start")
        await self.callback(99, "a:user:1")
        button = self.messages(99)[-1].reply_markup.inline_keyboard[0][0]
        self.assertEqual(button.url, "tg://user?id=1")

    async def test_broadcast_confirmation_does_not_add_users_joined_after_preview(self):
        await self.feed(1, "/start")
        await self.callback(99, "a:compose:all")
        await self.feed(99, "Announcement to the preview audience")
        data = await self.state(99).get_data()
        self.assertEqual(set(data["recipient_ids"]), {1, 99})
        await self.feed(2, "/start")
        await self.callback(99, f"a:send:{data['nonce']}")
        await asyncio.gather(*list(self.messaging._tasks.values()))
        self.assertTrue(
            any(m.text == "Announcement to the preview audience" for m in self.messages(1))
        )
        self.assertFalse(any("Announcement" in m.text for m in self.messages(2)))
