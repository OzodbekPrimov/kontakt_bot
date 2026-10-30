import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.application.lookup_worker import LookupWorker
from app.domain.models import DeliveryStatus, UserProfile
from app.infrastructure.database import SCHEMA, Database
from app.infrastructure.user_lookup import LookupFloodWait, ResolvedAccount


class FakeResolver:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def resolve(self, phone):
        self.calls.append(phone)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeGateway:
    def __init__(self):
        self.status = DeliveryStatus.SENT
        self.sent = []

    async def send_lookup(self, user_id, html):
        self.sent.append((user_id, html))
        return self.status


class ServerLookupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "bot.sqlite3"
        self.db = await Database.open(self.path)
        await self.db.touch_user(UserProfile(1, "User"))
        self.resolver = FakeResolver()
        self.gateway = FakeGateway()
        self.worker = LookupWorker(self.db, self.gateway, self.resolver, 5)

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def enqueue(self, message_id):
        return await self.db.enqueue_lookup(1, 1, message_id, "+998901234567", "+99890***4567", 5)

    async def test_found_charges_once_only_after_delivery(self):
        request_id, outcome = await self.enqueue(1)
        self.assertEqual(outcome, "queued")
        self.assertEqual((await self.db.lookup_balance(1))[:2], (5, 4))
        self.resolver.result = ResolvedAccount(777, "Ali", None, "ali777")
        self.assertTrue(await self.worker.process_one())
        self.assertEqual((await self.db.lookup_balance(1))[:2], (5, 4))
        self.assertTrue(await self.worker.process_one())
        self.assertEqual((await self.db.lookup_balance(1))[:2], (5, 4))
        self.assertIn("@ali777", self.gateway.sent[0][1])
        self.assertIn("https://t.me/ali777", self.gateway.sent[0][1])
        self.assertEqual(await self.enqueue(1), (request_id, "duplicate"))
        self.assertEqual((await self.db.lookup_balance(1))[:2], (5, 4))
        row = (await self.db._rows("SELECT phone,result_text,status FROM lookup_requests"))[0]
        self.assertEqual(row, {"phone": None, "result_text": None, "status": "sent"})

    async def test_unresolvable_does_not_charge_and_does_not_claim_absence(self):
        await self.enqueue(1)
        await self.worker.process_one()
        await self.worker.process_one()
        self.assertEqual((await self.db.lookup_balance(1))[:2], (5, 5))
        self.assertIn("maxfiylik", self.gateway.sent[0][1])
        stats = await self.db.lookup_status()
        self.assertEqual(stats["sent:0"], 1)

    async def test_delivery_unknown_is_never_charged_or_retried(self):
        await self.enqueue(1)
        self.resolver.result = ResolvedAccount(777, "Ali", None, None)
        await self.worker.process_one()
        self.gateway.status = DeliveryStatus.UNKNOWN
        await self.worker.process_one()
        self.assertEqual((await self.db.lookup_balance(1))[:2], (5, 5))
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertFalse(await self.worker.process_one())

    async def test_300_accepted_results_stop_at_the_plan_limit(self):
        await self.db.grant_lookup_plan(1, 300, 30, 99)
        for message_id in range(1, 301):
            self.assertEqual((await self.enqueue(message_id))[1], "queued")
        self.assertEqual((await self.enqueue(301))[1], "exhausted")
        self.assertEqual((await self.db.lookup_balance(1))[:2], (300, 0))

    async def test_active_plan_cannot_be_overwritten_accidentally(self):
        await self.db.grant_lookup_plan(1, 300, 30, 99)
        with self.assertRaisesRegex(ValueError, "active lookup plan"):
            await self.db.grant_lookup_plan(1, 300, 30, 99)

    async def test_free_plan_can_be_upgraded_without_changing_reserved_request(self):
        first, _ = await self.enqueue(1)
        await self.db.grant_lookup_plan(1, 300, 30, 99)
        self.assertEqual((await self.db.lookup_balance(1))[:2], (300, 300))
        second, outcome = await self.enqueue(2)
        self.assertEqual(outcome, "queued")
        plans = await self.db._rows("SELECT id,plan_id FROM lookup_requests ORDER BY id")
        self.assertNotEqual(plans[0]["plan_id"], plans[1]["plan_id"])
        self.assertEqual([row["id"] for row in plans], [first, second])

    async def test_restart_retries_resolution_but_not_uncertain_delivery(self):
        await self.enqueue(1)
        first = await self.db.claim_lookup()
        self.assertEqual(first["status"], "pending")
        await self.db.close()
        self.db = await Database.open(self.path)
        row = (await self.db._rows("SELECT status FROM lookup_requests"))[0]
        self.assertEqual(row["status"], "pending")
        await self.db._write(
            "UPDATE lookup_requests SET status='delivering' WHERE id=?", (first["id"],)
        )
        await self.db.close()
        self.db = await Database.open(self.path)
        row = (await self.db._rows("SELECT status,phone FROM lookup_requests"))[0]
        self.assertEqual(row, {"status": "unknown", "phone": None})
        self.assertEqual((await self.db.lookup_balance(1))[:2], (5, 5))

    async def test_flood_wait_is_persisted_for_the_account(self):
        await self.enqueue(1)
        self.resolver.result = LookupFloodWait(30)
        await self.worker.process_one()
        row = (await self.db._rows("SELECT status,due_at FROM lookup_requests"))[0]
        self.assertEqual(row["status"], "pending")
        self.assertFalse(await self.worker.process_one())
        await self.db.close()
        self.db = await Database.open(self.path)
        self.assertFalse(await self.db.claim_lookup())

    async def test_version_one_database_is_migrated_without_losing_users(self):
        await self.db.close()
        self.path.unlink()
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(SCHEMA)
            connection.execute("INSERT INTO users VALUES (1,'Existing',NULL,NULL,1,1,0)")
            connection.commit()
        finally:
            connection.close()
        self.db = await Database.open(self.path)
        self.assertEqual((await self.db.get_user(1)).first_name, "Existing")
        self.assertEqual((await self.enqueue(1))[1], "queued")
