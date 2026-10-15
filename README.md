# Kontakt Bot

Simple aiogram 3 bot: it accepts a phone number or Telegram contact and creates a **phone deep link** such as `https://t.me/+998901234567?profile`.

The bot does **not** look up, infer, store, or return someone else's username. Telegram resolves the link inside the clicking user's client, subject to Telegram privacy controls.

## What it does

1. A user sends a valid number, for example `+998901234567`, or shares a contact.
2. The bot validates and normalizes it to E.164.
3. The bot returns an inline **Telegram’da profilni ochish** button.
4. Telegram tries to open the matching profile/chat for the person who pressed the button.

The bot cannot determine whether the number has an account, cannot learn the result of client-side resolution, and cannot return a target username.

`/myprofile` is different: it returns the requesting user's own public username if they have one.

There are no application request quotas. Each submitted number or contact produces one button. Telegram's own API limits and profile privacy settings still apply.

Local numbers use Uzbekistan (`UZ`) by default. For other countries, send the full international number with its country code.

The application runs as a single polling process. No Redis, database, worker, scheduled task, or additional service is required. The domain handles phone validation and URL construction, the application service coordinates them, and Telegram handlers render the result.

## Local launch

```powershell
Copy-Item .env.example .env
# Set BOT_TOKEN in .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m app.main
```

The only required setting in `.env` is `BOT_TOKEN`:

```dotenv
BOT_TOKEN=your-botfather-token
```

Alternatively, run the bot with Docker:

```powershell
Copy-Item .env.example .env
# Set BOT_TOKEN in .env
docker compose up --build -d
```

## Commands

- `/start` — purpose and instructions
- `/help` — usage
- `/privacy` — data-handling statement
- `/myprofile` — the current user's own public profile link

## Privacy and operational rules

- The bot stores no phone numbers, contacts, or request history and logs no phone numbers.
- Phone numbers appear in the generated Telegram links and remain in Telegram chat history. Users can delete these messages themselves.
- There are no request counters, cooldowns, or daily quotas in the application.
- Keep the token in `.env`, which is ignored by Git. No user-account session, `api_id`, or `api_hash` is needed.
- Polling needs only outgoing internet access. No inbound port, domain, or webhook setup is required.

## Verification

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```
