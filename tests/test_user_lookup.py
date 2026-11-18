import unittest
from types import SimpleNamespace

from telethon.errors import FloodWaitError, PhoneNotOccupiedError

from app.infrastructure.user_lookup import (
    LookupFloodWait,
    UserLookupClient,
    wire_phone,
)


class FakeTelethon:
    def __init__(self, outcome, connected=True):
        self.outcome = outcome
        self.connected = connected
        self.requests = []
        self.connects = 0

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connects += 1
        self.connected = True

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def client_with(fake):
    client = UserLookupClient.__new__(UserLookupClient)
    client._client = fake
    return client


def resolved(user_id=777, username="ali777"):
    user = SimpleNamespace(id=user_id, first_name="Ali", last_name=None, username=username)
    return SimpleNamespace(peer=SimpleNamespace(user_id=user_id), users=[user])


class UserLookupClientTests(unittest.IsolatedAsyncioTestCase):
    def test_wire_phone_is_bare_digits(self):
        self.assertEqual(wire_phone("+998 94 373-75-25"), "998943737525")
        with self.assertRaises(ValueError):
            wire_phone("+")

    async def test_request_is_sent_without_plus(self):
        fake = FakeTelethon(resolved())
        account = await client_with(fake).resolve("+998943737525")
        self.assertEqual(fake.requests[0].phone, "998943737525")
        self.assertEqual((account.user_id, account.username), (777, "ali777"))

    async def test_hidden_number_is_reported_as_not_visible(self):
        fake = FakeTelethon(PhoneNotOccupiedError(request=None))
        self.assertIsNone(await client_with(fake).resolve("+998943737525"))

    async def test_flood_wait_is_translated(self):
        error = FloodWaitError(request=None, capture=42)
        fake = FakeTelethon(error)
        with self.assertRaises(LookupFloodWait) as caught:
            await client_with(fake).resolve("+998943737525")
        self.assertEqual(caught.exception.seconds, 42)

    async def test_dropped_connection_is_restored_before_the_request(self):
        fake = FakeTelethon(resolved(), connected=False)
        await client_with(fake).resolve("+998943737525")
        self.assertEqual(fake.connects, 1)
        self.assertEqual(len(fake.requests), 1)
