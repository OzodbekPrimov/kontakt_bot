import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.application.messaging_service import MessagingService
from app.application.support_service import SupportService
from app.backup import create_backup
from app.domain.models import TASHKENT, DeliveryStatus, Payload, UserProfile
from app.infrastructure.database import Database


class FakeGateway:
    def __init__(self):
        self.sent = []
        self.status = DeliveryStatus.SENT
        self.started = None
        self.release = None

    async def send(self, user_id, payload):
        self.sent.append((user_id, payload))
        if self.started is not None:
            self.started.set()
            await self.release.wait()
        return self.status


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "bot.sqlite3"
        self.db = await Database.open(self.path)
        self.gateway = FakeGateway()
        self.support = SupportService(self.db, self.gateway, {99})
        self.messaging = MessagingService(self.db, self.gateway, {99})
        for user_id in (1, 2, 99):
            await self.db.touch_user(
                UserProfile(user_id, f"User {user_id}", username=f"u{user_id}")
            )

    async def asyncTearDown(self):
        await self.messaging.close()
        await self.db.close()
        self.directory.cleanup()

    async def test_usage_is_deduplicated_and_counts_only_the_sender(self):
        for _ in range(2):
            await self.db.record_usage(1, 1, 10, "contact", True)
        await self.db.record_usage(1, 1, 11, "phone", False)
        stats = await self.db.user_stats(1)
        self.assertEqual((stats.contacts, stats.phone_requests, stats.successful), (1, 1, 1))
        self.assertEqual((await self.db.user_stats(2)).contacts, 0)
        total = await self.db.global_stats()
        self.assertEqual((total.total, total.active_today, total.new_today), (3, 3, 3))

    async def test_data_survives_restart_and_user_is_not_registered_twice(self):
        await self.db.record_usage(1, 1, 10, "contact", True)
        await self.db.close()
        self.db = await Database.open(self.path)
        await self.db.touch_user(UserProfile(1, "Updated", username="newname"))
        self.assertEqual((await self.db.global_stats()).total, 3)
        self.assertEqual((await self.db.get_user(1)).username, "newname")
        self.assertEqual((await self.db.user_stats(1)).contacts, 1)

    async def test_search_handles_large_ids_and_does_not_interpolate_sql(self):
        for query in ("9" * 64, "9223372036854775808", "' OR 1=1 --", "²"):
            self.assertEqual((await self.db.list_users(0, query))[1], 0)
        self.assertEqual((await self.db.list_users(0, "@U1"))[1], 1)

    async def test_ticket_deduplication_and_new_ticket_after_closing(self):
        first, created = await self.support.submit(1, 1, 1, Payload("Hello"))
        duplicate, repeated = await self.support.submit(1, 1, 1, Payload("Hello"))
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(len(await self.db.ticket_messages(first.id)), 1)
        self.assertTrue(await self.db.close_ticket(first.id, 99))
        new, _ = await self.support.submit(1, 1, 2, Payload("Next question"))
        self.assertNotEqual(first.id, new.id)

    async def test_unauthorized_admin_actions_do_not_send_or_create_campaigns(self):
        with self.assertRaises(PermissionError):
            await self.messaging.create(1, Payload("Spam"), 2)
        with self.assertRaises(PermissionError):
            await self.support.reply(1, 1, 1, 1, Payload("Fake admin"))
        self.assertEqual(await self.db.list_campaigns(), [])
        self.assertEqual(self.gateway.sent, [])

    async def test_reply_is_sent_to_ticket_owner_not_the_chosen_contact(self):
        ticket, _ = await self.support.submit(1, 1, 1, Payload("Question"))
        result = await self.support.reply(99, ticket.id, 99, 2, Payload("Answer"))
        self.assertEqual(result, DeliveryStatus.SENT)
        self.assertEqual(self.gateway.sent[0][0], 1)
        self.assertEqual((await self.db.get_ticket(ticket.id)).status, "answered")
        self.assertIsNone(await self.support.reply(99, ticket.id, 99, 2, Payload("Answer")))

    async def test_two_replies_cannot_be_in_flight_for_the_same_ticket(self):
        ticket, _ = await self.support.submit(1, 1, 1, Payload("Question"))
        self.gateway.started, self.gateway.release = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(self.support.reply(99, ticket.id, 99, 2, Payload("A")))
        await self.gateway.started.wait()
        self.assertIsNone(await self.support.reply(99, ticket.id, 99, 3, Payload("B")))
        self.assertFalse(await self.db.close_ticket(ticket.id, 99))
        self.gateway.release.set()
        self.assertEqual(await pending, DeliveryStatus.SENT)

    async def test_new_user_message_during_reply_remains_unanswered(self):
        ticket, _ = await self.support.submit(1, 1, 1, Payload("Question"))
        self.gateway.started, self.gateway.release = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(self.support.reply(99, ticket.id, 99, 2, Payload("A")))
        await self.gateway.started.wait()
        await self.support.submit(1, 1, 3, Payload("Another question"))
        self.gateway.release.set()
        await pending
        self.assertEqual((await self.db.get_ticket(ticket.id)).status, "open")

    async def test_interrupted_campaign_skips_sent_and_uncertain_deliveries(self):
        campaign = await self.messaging.create(99, Payload("Announcement"), None)
        self.assertTrue(await self.db.claim_campaign(campaign.id))
        first = await self.db.next_recipient(campaign.id)
        await self.db.complete_delivery(campaign.id, first, "sent")
        uncertain = await self.db.next_recipient(campaign.id)
        await self.db.close()
        self.db = await Database.open(self.path)
        self.messaging = MessagingService(self.db, self.gateway, {99})
        counts = await self.db.campaign_counts(campaign.id)
        self.assertEqual(counts, {"sent": 1, "unknown": 1, "pending": 1})
        self.assertTrue(await self.messaging.start(99, campaign.id))
        tasks = list(self.messaging._tasks.values())
        await asyncio.gather(*tasks)
        recipients = [uid for uid, payload in self.gateway.sent if payload.text == "Announcement"]
        self.assertNotIn(first, recipients)
        self.assertNotIn(uncertain, recipients)
        self.assertEqual(len(recipients), 1)

    async def test_blocked_recipient_does_not_stop_the_campaign(self):
        self.gateway.status = DeliveryStatus.BLOCKED
        campaign = await self.messaging.create(99, Payload("Announcement"), None)
        await self.messaging.start(99, campaign.id)
        await asyncio.gather(*list(self.messaging._tasks.values()))
        self.assertEqual((await self.db.campaign_counts(campaign.id))["blocked"], 3)
        self.assertTrue((await self.db.get_user(1)).blocked)

    async def test_resume_is_rejected_while_previous_task_is_still_sending(self):
        self.gateway.started, self.gateway.release = asyncio.Event(), asyncio.Event()
        campaign = await self.messaging.create(99, Payload("Announcement"), 1)
        await self.messaging.start(99, campaign.id)
        await self.gateway.started.wait()
        await self.messaging.pause(99, campaign.id)
        self.assertFalse(await self.messaging.start(99, campaign.id))
        tasks = list(self.messaging._tasks.values())
        self.gateway.release.set()
        await asyncio.gather(*tasks)

    async def test_broadcast_uses_the_confirmed_recipient_snapshot(self):
        snapshot = await self.db.recipient_ids()
        await self.db.touch_user(UserProfile(1234, "Joined after preview"))
        campaign = await self.messaging.create(99, Payload("Snapshot"), None, snapshot)
        await self.messaging.start(99, campaign.id)
        await asyncio.gather(*list(self.messaging._tasks.values()))
        recipients = [uid for uid, payload in self.gateway.sent if payload.text == "Snapshot"]
        self.assertEqual(set(recipients), set(snapshot))
        self.assertNotIn(1234, recipients)

    async def test_shutdown_marks_in_flight_send_unknown_and_leaves_the_rest_pending(self):
        self.gateway.started, self.gateway.release = asyncio.Event(), asyncio.Event()
        campaign = await self.messaging.create(99, Payload("Announcement"), None)
        await self.messaging.start(99, campaign.id)
        await self.gateway.started.wait()
        await self.messaging.close()
        self.assertEqual((await self.db.get_campaign(campaign.id)).status, "paused")
        self.assertEqual(await self.db.campaign_counts(campaign.id), {"unknown": 1, "pending": 2})

    async def test_today_uses_tashkent_midnight(self):
        now = datetime(2026, 10, 16, 0, 30, tzinfo=TASHKENT)
        today = int(now.replace(hour=0, minute=0).timestamp())
        await self.db._write("UPDATE users SET joined_at=?,last_seen=?", (today - 100, today - 100))
        await self.db._write(
            "UPDATE users SET joined_at=?,last_seen=? WHERE telegram_id=1", (today + 10, today + 10)
        )
        with patch("app.infrastructure.database.datetime") as clock:
            clock.now.return_value = now
            with patch("app.infrastructure.database.timestamp", return_value=int(now.timestamp())):
                stats = await self.db.global_stats()
        self.assertEqual((stats.active_today, stats.new_today), (1, 1))

    async def test_manual_backup_includes_wal_data_and_does_not_modify_the_source(self):
        await self.db.record_usage(1, 1, 10, "contact", True)
        backup_path = create_backup(self.path)
        backup = await Database.open(backup_path)
        try:
            self.assertEqual((await backup.global_stats()).total, 3)
            self.assertEqual((await backup.user_stats(1)).contacts, 1)
        finally:
            await backup.close()
        self.assertEqual((await self.db.user_stats(1)).contacts, 1)
