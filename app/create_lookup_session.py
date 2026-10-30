"""Create a dedicated Telethon user session without printing its secret."""

from __future__ import annotations

import asyncio
import getpass
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    path = Path("data/lookup-session.txt")
    if path.exists():
        raise SystemExit("Session file already exists; refusing to overwrite it")
    api_id = int(input("Telegram API ID: ").strip())
    api_hash = getpass.getpass("Telegram API hash: ").strip()
    phone = input("Your Telegram phone number (+country...): ").strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        await client.start(
            phone=lambda: phone,
            code_callback=lambda: input("Telegram login code: ").strip(),
            password=lambda: getpass.getpass("Two-step password (if asked): "),
        )
        me = await client.get_me()
        if me is None or me.bot:
            raise SystemExit("A regular user account is required")
        secret = client.session.save()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as session_file:
            session_file.write(secret)
        print(f"User session saved to {path}; keep this file private.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
