from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import aiosqlite

from app.domain.models import (
    TASHKENT,
    Campaign,
    GlobalStats,
    Payload,
    SupportMessage,
    Ticket,
    UsageStats,
    UserProfile,
    timestamp,
)

PAGE_SIZE = 8

SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE users (
    telegram_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT,
    username TEXT,
    joined_at INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    blocked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX users_seen ON users(last_seen);
CREATE TABLE usage_events (
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('contact', 'phone', 'selection')),
    success INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(chat_id, message_id, kind)
);
CREATE INDEX usage_user ON usage_events(user_id);
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    status TEXT NOT NULL DEFAULT 'open',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX one_open_ticket ON tickets(user_id) WHERE status != 'closed';
CREATE TABLE support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    sender_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(chat_id, message_id)
);
CREATE UNIQUE INDEX one_reply_in_flight ON support_messages(ticket_id)
    WHERE role = 'admin' AND status = 'sending';
CREATE TABLE campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL
);
CREATE TABLE deliveries (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(campaign_id, user_id)
);
CREATE INDEX deliveries_status ON deliveries(campaign_id, status);
CREATE TABLE admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
PRAGMA user_version = 1;
COMMIT;
"""


class Database:
    """One SQLite connection, short serialized transactions, no Telegram calls inside them."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> Database:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path, isolation_level=None)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA busy_timeout = 5000")
            async with connection.execute("PRAGMA user_version") as cursor:
                version = (await cursor.fetchone())[0]
            if version == 0:
                await connection.executescript(SCHEMA)
            elif version != 1:
                raise RuntimeError("Unsupported database version; update the application")
            # A send may have reached Telegram before the process stopped. Never blindly resend it.
            await connection.execute(
                "UPDATE deliveries SET status = 'unknown' WHERE status = 'sending'"
            )
            await connection.execute(
                "UPDATE support_messages SET status = 'unknown' WHERE status = 'sending'"
            )
            await connection.execute(
                "UPDATE campaigns SET status = 'interrupted' WHERE status = 'running'"
            )
        except BaseException:
            await connection.close()
            raise
        return cls(connection)

    async def close(self) -> None:
        await self._connection.close()

    async def _rows(self, sql: str, parameters: tuple = ()) -> list[dict]:
        async with self._lock, self._connection.execute(sql, parameters) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def _write(self, sql: str, parameters: tuple = ()) -> None:
        async with self._lock:
            await self._connection.execute(sql, parameters)

    async def touch_user(self, profile: UserProfile) -> None:
        now = timestamp()
        await self._write(
            """INSERT INTO users
               (telegram_id, first_name, last_name, username, joined_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
               first_name=excluded.first_name, last_name=excluded.last_name,
               username=excluded.username, last_seen=excluded.last_seen, blocked=0""",
            (
                profile.telegram_id,
                profile.first_name,
                profile.last_name,
                profile.username,
                now,
                now,
            ),
        )

    async def get_user(self, user_id: int) -> UserProfile | None:
        rows = await self._rows("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        return UserProfile(**rows[0]) if rows else None

    async def list_users(self, page: int, query: str = "") -> tuple[list[UserProfile], int]:
        # Search is exact ID/username, never arbitrary SQL or a phone-to-person index.
        where = " WHERE telegram_id = ? OR lower(username) = ?" if query else ""
        numeric_id = int(query) if query.isascii() and query.isdecimal() and len(query) <= 19 else 0
        numeric_id = numeric_id if numeric_id < 2**63 else 0
        params = (numeric_id, query.lstrip("@").lower()) if query else ()
        rows = await self._rows(
            f"SELECT * FROM users{where} ORDER BY joined_at DESC, telegram_id LIMIT ? OFFSET ?",
            (*params, PAGE_SIZE, max(0, page) * PAGE_SIZE),
        )
        total = await self._rows(f"SELECT count(*) AS n FROM users{where}", params)
        return [UserProfile(**row) for row in rows], total[0]["n"]

    async def set_blocked(self, user_id: int, blocked: bool) -> None:
        await self._write("UPDATE users SET blocked = ? WHERE telegram_id = ?", (blocked, user_id))

    async def record_usage(
        self, user_id: int, chat_id: int, message_id: int, kind: str, success: bool
    ) -> None:
        await self._write(
            "INSERT OR IGNORE INTO usage_events VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, message_id, kind, success, timestamp()),
        )

    async def user_stats(self, user_id: int) -> UsageStats:
        return await self._usage(user_id)

    async def _usage(self, user_id: int | None = None) -> UsageStats:
        where = " WHERE user_id = ?" if user_id is not None else ""
        rows = await self._rows(
            """SELECT coalesce(sum(kind='contact'), 0) AS contacts,
               coalesce(sum(kind='phone'), 0) AS phone_requests,
               coalesce(sum(kind='selection'), 0) AS user_selections,
               coalesce(sum(success=1), 0) AS successful,
               coalesce(sum(success=0), 0) AS unsuccessful FROM usage_events"""
            + where,
            (user_id,) if user_id is not None else (),
        )
        return UsageStats(**rows[0])

    async def global_stats(self) -> GlobalStats:
        now = timestamp()
        today = int(
            datetime.now(TASHKENT).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        rows = await self._rows(
            """SELECT count(*) AS total,
               coalesce(sum(joined_at>=?),0) AS new_today,
               coalesce(sum(last_seen>=?),0) AS active_today,
               coalesce(sum(last_seen>=?),0) AS active_week,
               coalesce(sum(last_seen>=?),0) AS active_month,
               coalesce(sum(blocked),0) AS blocked FROM users""",
            (today, today, now - 7 * 86400, now - 30 * 86400),
        )
        return GlobalStats(**rows[0], usage=await self._usage())

    async def add_ticket_message(
        self, user_id: int, chat_id: int, message_id: int, payload: Payload
    ) -> tuple[Ticket, bool]:
        now = timestamp()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                async with self._connection.execute(
                    "SELECT ticket_id FROM support_messages WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id),
                ) as cursor:
                    duplicate = await cursor.fetchone()
                if duplicate:
                    ticket_id, created = duplicate[0], False
                else:
                    async with self._connection.execute(
                        "SELECT id FROM tickets WHERE user_id=? AND status!='closed'", (user_id,)
                    ) as cursor:
                        existing = await cursor.fetchone()
                    if existing:
                        ticket_id = existing[0]
                    else:
                        cursor = await self._connection.execute(
                            "INSERT INTO tickets(user_id,created_at,updated_at) VALUES (?,?,?)",
                            (user_id, now, now),
                        )
                        ticket_id = cursor.lastrowid
                    await self._connection.execute(
                        """INSERT INTO support_messages
                           (ticket_id,sender_id,role,chat_id,message_id,payload,status,created_at)
                           VALUES (?,?,'user',?,?,?,'received',?)""",
                        (ticket_id, user_id, chat_id, message_id, payload.serialize(), now),
                    )
                    await self._connection.execute(
                        "UPDATE tickets SET status='open',updated_at=? WHERE id=?", (now, ticket_id)
                    )
                    created = True
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise
        ticket = await self.get_ticket(ticket_id)
        assert ticket is not None
        return ticket, created

    async def get_ticket(self, ticket_id: int) -> Ticket | None:
        rows = await self._rows("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        return Ticket(**rows[0]) if rows else None

    async def list_tickets(self, page: int) -> tuple[list[Ticket], int]:
        rows = await self._rows(
            """SELECT * FROM tickets ORDER BY status='closed', updated_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (PAGE_SIZE, max(0, page) * PAGE_SIZE),
        )
        total = await self._rows("SELECT count(*) AS n FROM tickets")
        return [Ticket(**row) for row in rows], total[0]["n"]

    async def ticket_messages(self, ticket_id: int) -> list[SupportMessage]:
        rows = await self._rows(
            """SELECT id,ticket_id,sender_id,role,payload,status,created_at
               FROM support_messages WHERE ticket_id=? ORDER BY id DESC LIMIT 5""",
            (ticket_id,),
        )
        return [
            SupportMessage(**{**row, "payload": Payload.deserialize(row["payload"])})
            for row in reversed(rows)
        ]

    async def close_ticket(self, ticket_id: int, admin_id: int) -> bool:
        async with self._lock:
            cursor = await self._connection.execute(
                """UPDATE tickets SET status='closed', updated_at=? WHERE id=? AND status!='closed'
                   AND NOT EXISTS (SELECT 1 FROM support_messages
                     WHERE ticket_id=? AND status='sending')""",
                (timestamp(), ticket_id, ticket_id),
            )
            changed = cursor.rowcount == 1
            if changed:
                await self._audit(admin_id, "close_ticket", ticket_id)
        return changed

    async def reserve_reply(
        self, ticket_id: int, admin_id: int, chat_id: int, message_id: int, payload: Payload
    ) -> int | None:
        async with self._lock:
            try:
                cursor = await self._connection.execute(
                    """INSERT INTO support_messages
                       (ticket_id,sender_id,role,chat_id,message_id,payload,status,created_at)
                       SELECT id,?,'admin',?,?,?,'sending',? FROM tickets
                       WHERE id=? AND status!='closed'""",
                    (admin_id, chat_id, message_id, payload.serialize(), timestamp(), ticket_id),
                )
            except aiosqlite.IntegrityError:
                return None
            if cursor.rowcount == 0:
                return None
            await self._audit(admin_id, "reply_ticket", ticket_id)
            return cursor.lastrowid

    async def finish_reply(self, reply_id: int, status: str) -> None:
        async with self._lock:
            await self._connection.execute(
                "UPDATE support_messages SET status=? WHERE id=?", (status, reply_id)
            )
            if status == "sent":
                await self._connection.execute(
                    """UPDATE tickets SET status='answered', updated_at=?
                       WHERE id=(SELECT ticket_id FROM support_messages WHERE id=?)
                       AND NOT EXISTS (SELECT 1 FROM support_messages AS newer
                         WHERE newer.ticket_id=tickets.id AND newer.role='user' AND newer.id>?)""",
                    (timestamp(), reply_id, reply_id),
                )

    async def _audit(self, admin_id: int, action: str, target_id: int) -> None:
        await self._connection.execute(
            "INSERT INTO admin_actions(admin_id,action,target_id,created_at) VALUES (?,?,?,?)",
            (admin_id, action, target_id, timestamp()),
        )

    async def create_campaign(
        self,
        admin_id: int,
        payload: Payload,
        target_id: int | None,
        recipient_ids: list[int] | None = None,
    ) -> Campaign:
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._connection.execute(
                    "INSERT INTO campaigns(admin_id,payload,created_at) VALUES (?,?,?)",
                    (admin_id, payload.serialize(), timestamp()),
                )
                campaign_id = cursor.lastrowid
                if target_id is None and recipient_ids is not None:
                    await self._connection.executemany(
                        """INSERT INTO deliveries(campaign_id,user_id,updated_at)
                           SELECT ?,telegram_id,? FROM users WHERE telegram_id=? AND blocked=0""",
                        [(campaign_id, timestamp(), uid) for uid in set(recipient_ids)],
                    )
                elif target_id is None:
                    await self._connection.execute(
                        """INSERT INTO deliveries(campaign_id,user_id,updated_at)
                           SELECT ?,telegram_id,? FROM users WHERE blocked=0""",
                        (campaign_id, timestamp()),
                    )
                else:
                    await self._connection.execute(
                        "INSERT INTO deliveries(campaign_id,user_id,updated_at) VALUES (?,?,?)",
                        (campaign_id, target_id, timestamp()),
                    )
                await self._audit(admin_id, "create_campaign", campaign_id)
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise
        return Campaign(campaign_id, admin_id, payload, "pending", timestamp())

    async def get_campaign(self, campaign_id: int) -> Campaign | None:
        rows = await self._rows("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        if not rows:
            return None
        row = rows[0]
        return Campaign(**{**row, "payload": Payload.deserialize(row["payload"])})

    async def list_campaigns(self) -> list[Campaign]:
        rows = await self._rows("SELECT * FROM campaigns ORDER BY id DESC LIMIT 10")
        return [Campaign(**{**row, "payload": Payload.deserialize(row["payload"])}) for row in rows]

    async def claim_campaign(self, campaign_id: int) -> bool:
        async with self._lock:
            cursor = await self._connection.execute(
                """UPDATE campaigns SET status='running' WHERE id=?
                   AND status IN ('pending','paused','interrupted')
                   AND NOT EXISTS (SELECT 1 FROM deliveries
                     WHERE campaign_id=? AND status='sending')
                   AND NOT EXISTS (SELECT 1 FROM campaigns WHERE status='running')""",
                (campaign_id, campaign_id),
            )
            return cursor.rowcount == 1

    async def pause_campaign(self, campaign_id: int) -> None:
        await self._write(
            "UPDATE campaigns SET status='paused' WHERE id=? AND status='running'", (campaign_id,)
        )

    async def next_recipient(self, campaign_id: int) -> int | None:
        async with self._lock:
            async with self._connection.execute(
                """SELECT user_id FROM deliveries JOIN campaigns ON campaigns.id=campaign_id
                   WHERE campaign_id=? AND campaigns.status='running'
                     AND deliveries.status='pending'
                   ORDER BY user_id LIMIT 1""",
                (campaign_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            await self._connection.execute(
                "UPDATE deliveries SET status='sending',updated_at=? "
                "WHERE campaign_id=? AND user_id=?",
                (timestamp(), campaign_id, row[0]),
            )
            return row[0]

    async def complete_delivery(self, campaign_id: int, user_id: int, status: str) -> None:
        await self._write(
            "UPDATE deliveries SET status=?,updated_at=? WHERE campaign_id=? AND user_id=?",
            (status, timestamp(), campaign_id, user_id),
        )

    async def finish_campaign(self, campaign_id: int) -> None:
        await self._write(
            """UPDATE campaigns SET status='completed' WHERE id=? AND status='running'
               AND NOT EXISTS (SELECT 1 FROM deliveries WHERE campaign_id=?
                 AND status IN ('pending','sending'))""",
            (campaign_id, campaign_id),
        )

    async def campaign_counts(self, campaign_id: int) -> dict[str, int]:
        rows = await self._rows(
            "SELECT status,count(*) AS n FROM deliveries WHERE campaign_id=? GROUP BY status",
            (campaign_id,),
        )
        return {row["status"]: row["n"] for row in rows}

    async def recipient_ids(self) -> list[int]:
        return [
            row["telegram_id"]
            for row in await self._rows(
                "SELECT telegram_id FROM users WHERE blocked=0 ORDER BY telegram_id"
            )
        ]
