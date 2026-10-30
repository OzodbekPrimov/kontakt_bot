from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResolvedAccount:
    user_id: int
    first_name: str
    last_name: str | None
    username: str | None


class LookupUnavailable(Exception):
    """The account cannot run a lookup right now."""


class LookupFloodWait(Exception):
    def __init__(self, seconds: int) -> None:
        self.seconds = max(1, seconds)
        super().__init__("Telegram requested a lookup delay")


class UserLookupClient:
    """A single, explicitly configured user session. No interactive login in the bot."""

    def __init__(self, api_id: int, api_hash: str, session: str) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        self._client = TelegramClient(
            StringSession(session),
            api_id,
            api_hash,
            flood_sleep_threshold=0,
            request_retries=0,
            receive_updates=False,
        )

    async def connect(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            raise LookupUnavailable("User session is not authorized")
        me = await self._client.get_me()
        if me is None or me.bot:
            await self._client.disconnect()
            raise LookupUnavailable("Lookup requires a user session")

    async def close(self) -> None:
        await self._client.disconnect()

    async def resolve(self, phone: str) -> ResolvedAccount | None:
        from telethon import functions
        from telethon.errors import FloodWaitError, PhoneNotOccupiedError

        try:
            result = await self._client(functions.contacts.ResolvePhoneRequest(phone=phone))
        except PhoneNotOccupiedError:
            return None
        except FloodWaitError as exc:
            raise LookupFloodWait(exc.seconds) from None
        user_id = getattr(result.peer, "user_id", None)
        user = next((item for item in result.users if item.id == user_id), None)
        if user is None:
            raise LookupUnavailable("Resolved peer is missing from Telegram response")
        return ResolvedAccount(user.id, user.first_name or "", user.last_name, user.username)
