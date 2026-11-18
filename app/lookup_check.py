"""Check one number against the configured lookup session, outside the bot queue.

Usage: python -m app.lookup_check +998901234567
Use sparingly: every run is a real resolvePhone call and counts against Telegram's limit.
"""

from __future__ import annotations

import asyncio
import sys

from app.config import Settings
from app.domain.exceptions import InvalidPhoneNumber
from app.domain.phone_number import PhoneNumber
from app.infrastructure.user_lookup import LookupFloodWait, UserLookupClient


async def check(raw_phone: str) -> int:
    try:
        phone = PhoneNumber.parse(raw_phone)
    except InvalidPhoneNumber:
        print("Raqam formati noto‘g‘ri.")
        return 2
    settings = Settings()
    if not settings.lookup_enabled:
        print("LOOKUP_ENABLED=false — server qidiruvi sozlanmagan.")
        return 2
    session = (
        settings.lookup_session.get_secret_value()
        if settings.lookup_session
        else settings.lookup_session_file.read_text(encoding="utf-8").strip()
    )
    client = UserLookupClient(
        settings.lookup_api_id, settings.lookup_api_hash.get_secret_value(), session
    )
    await client.connect()
    try:
        me = await client._client.get_me()
        print(f"Lookup akkaunti: ID {me.id}, @{me.username or '-'}")
        account = await client.resolve(phone.e164)
    except LookupFloodWait as exc:
        print(f"Telegram FLOOD_WAIT: {exc.seconds} soniya kuting.")
        return 1
    finally:
        await client.close()
    if account is None:
        print(f"{phone.masked}: ko‘rinmadi (PHONE_NOT_OCCUPIED).")
        return 1
    print(f"{phone.masked}: topildi — ID {account.user_id}, @{account.username or '-'}")
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Foydalanish: python -m app.lookup_check +998901234567")
    raise SystemExit(asyncio.run(check(sys.argv[1])))


if __name__ == "__main__":
    main()
