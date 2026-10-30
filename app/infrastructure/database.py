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

LOOKUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS lookup_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    capacity INTEGER NOT NULL CHECK(capacity > 0),
    starts_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    created_by INTEGER,
    CHECK(expires_at > starts_at)
);
CREATE INDEX IF NOT EXISTS lookup_plans_user ON lookup_plans(user_id, expires_at);
CREATE TABLE IF NOT EXISTS lookup_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES lookup_plans(id),
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    phone TEXT,
    masked_phone TEXT NOT NULL,
    status TEXT NOT NULL,
    due_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    found INTEGER NOT NULL DEFAULT 0,
    result_text TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS lookup_requests_work ON lookup_requests(status, due_at, id);
CREATE TABLE IF NOT EXISTS lookup_quota_events (
    request_id INTEGER PRIMARY KEY REFERENCES lookup_requests(id),
    plan_id INTEGER NOT NULL REFERENCES lookup_plans(id),
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lookup_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    next_allowed_at INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO lookup_state(id, next_allowed_at) VALUES (1, 0);
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
            elif version not in (1, 2):
                raise RuntimeError("Unsupported database version; update the application")
            if version < 2:
                await connection.executescript(LOOKUP_SCHEMA)
                await connection.execute("PRAGMA user_version = 2")
            # A lookup can be retried. A delivery may already have reached the user.
            await connection.execute(
                "UPDATE lookup_requests SET status='pending' WHERE status='resolving'"
            )
            await connection.execute(
                "UPDATE lookup_requests SET status='unknown',phone=NULL,result_text=NULL "
                "WHERE status='delivering'"
            )
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

    async def grant_lookup_plan(self, user_id: int, capacity: int, days: int, admin_id: int) -> int:
        if capacity < 1 or days < 1:
            raise ValueError("Capacity and days must be positive")
        now = timestamp()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                async with self._connection.execute(
                    "SELECT 1 FROM users WHERE telegram_id=?", (user_id,)
                ) as cursor:
                    if await cursor.fetchone() is None:
                        raise ValueError("The user must start the bot first")
                async with self._connection.execute(
                    "SELECT 1 FROM lookup_plans WHERE user_id=? AND expires_at>? "
                    "AND created_by IS NOT NULL LIMIT 1",
                    (user_id, now),
                ) as cursor:
                    if await cursor.fetchone() is not None:
                        raise ValueError("An active lookup plan already exists")
                cursor = await self._connection.execute(
                    "INSERT INTO lookup_plans(user_id,capacity,starts_at,expires_at,created_by) "
                    "VALUES (?,?,?,?,?)",
                    (user_id, capacity, now, now + days * 86400, admin_id),
                )
                await self._audit(admin_id, "grant_lookup_plan", cursor.lastrowid)
                await self._connection.commit()
                return cursor.lastrowid
            except BaseException:
                await self._connection.rollback()
                raise

    async def lookup_balance(self, user_id: int) -> tuple[int, int, int] | None:
        now = timestamp()
        rows = await self._rows(
            "SELECT id,capacity,expires_at FROM lookup_plans WHERE user_id=? "
            "AND starts_at<=? AND expires_at>? ORDER BY id DESC LIMIT 1",
            (user_id, now, now),
        )
        if not rows:
            return None
        plan = rows[0]
        counts = await self._rows(
            "SELECT (SELECT count(*) FROM lookup_quota_events WHERE plan_id=?) + "
            "(SELECT count(*) FROM lookup_requests WHERE plan_id=? AND status IN "
            "('pending','resolving','delivery_pending','delivering')) AS used",
            (plan["id"], plan["id"]),
        )
        return plan["capacity"], max(0, plan["capacity"] - counts[0]["used"]), plan["expires_at"]

    async def lookup_status(self) -> dict[str, int]:
        rows = await self._rows(
            "SELECT status,found,count(*) AS n FROM lookup_requests GROUP BY status,found"
        )
        result = {f"{row['status']}:{row['found']}": row["n"] for row in rows}
        state = await self._rows("SELECT next_allowed_at FROM lookup_state WHERE id=1")
        result["next_allowed_at"] = state[0]["next_allowed_at"]
        return result

    async def enqueue_lookup(
        self,
        user_id: int,
        chat_id: int,
        message_id: int,
        phone: str,
        masked_phone: str,
        free_limit: int,
    ) -> tuple[int | None, str]:
        now = timestamp()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                async with self._connection.execute(
                    "SELECT id FROM lookup_requests WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id),
                ) as cursor:
                    duplicate = await cursor.fetchone()
                if duplicate:
                    await self._connection.commit()
                    return duplicate[0], "duplicate"
                async with self._connection.execute(
                    "SELECT id,capacity FROM lookup_plans WHERE user_id=? "
                    "AND starts_at<=? AND expires_at>? ORDER BY id DESC LIMIT 1",
                    (user_id, now, now),
                ) as cursor:
                    plan = await cursor.fetchone()
                if plan is None:
                    async with self._connection.execute(
                        "SELECT 1 FROM lookup_plans WHERE user_id=? LIMIT 1", (user_id,)
                    ) as cursor:
                        had_plan = await cursor.fetchone() is not None
                    if not had_plan and free_limit > 0:
                        cursor = await self._connection.execute(
                            "INSERT INTO lookup_plans(user_id,capacity,starts_at,expires_at) "
                            "VALUES (?,?,?,?)",
                            (user_id, free_limit, now, now + 30 * 86400),
                        )
                        plan_id, capacity = cursor.lastrowid, free_limit
                    else:
                        await self._connection.commit()
                        return None, "no_plan"
                else:
                    plan_id, capacity = plan
                async with self._connection.execute(
                    "SELECT (SELECT count(*) FROM lookup_quota_events WHERE plan_id=?) + "
                    "(SELECT count(*) FROM lookup_requests WHERE plan_id=? AND status IN "
                    "('pending','resolving','delivery_pending','delivering'))",
                    (plan_id, plan_id),
                ) as cursor:
                    used = (await cursor.fetchone())[0]
                if used >= capacity:
                    await self._connection.commit()
                    return None, "exhausted"
                cursor = await self._connection.execute(
                    "INSERT INTO lookup_requests(plan_id,user_id,chat_id,message_id,phone,"
                    "masked_phone,status,due_at,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,'pending',?,?,?)",
                    (plan_id, user_id, chat_id, message_id, phone, masked_phone, now, now, now),
                )
                await self._connection.commit()
                return cursor.lastrowid, "queued"
            except BaseException:
                await self._connection.rollback()
                raise

    async def claim_lookup(self) -> dict | None:
        now = timestamp()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                async with self._connection.execute(
                    "SELECT next_allowed_at FROM lookup_state WHERE id=1"
                ) as cursor:
                    next_allowed = (await cursor.fetchone())[0]
                if next_allowed > now:
                    await self._connection.commit()
                    return None
                async with self._connection.execute(
                    "SELECT * FROM lookup_requests WHERE status='pending' AND due_at<=? "
                    "ORDER BY due_at,id LIMIT 1",
                    (now,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    await self._connection.commit()
                    return None
                await self._connection.execute(
                    "UPDATE lookup_requests SET status='resolving',"
                    "attempts=attempts+1,updated_at=? "
                    "WHERE id=?",
                    (now, row["id"]),
                )
                await self._connection.execute(
                    "UPDATE lookup_state SET next_allowed_at=? WHERE id=1", (now + 4,)
                )
                await self._connection.commit()
                return dict(row)
            except BaseException:
                await self._connection.rollback()
                raise

    async def defer_lookup(self, request_id: int, seconds: int) -> None:
        now = timestamp()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                due = now + max(1, seconds) + 1
                await self._connection.execute(
                    "UPDATE lookup_requests SET status='pending',due_at=?,updated_at=? "
                    "WHERE id=? AND status='resolving'",
                    (due, now, request_id),
                )
                await self._connection.execute(
                    "UPDATE lookup_state SET next_allowed_at=max(next_allowed_at,?) WHERE id=1",
                    (due,),
                )
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise

    async def complete_lookup_resolution(self, request_id: int, found: bool, text: str) -> None:
        await self._write(
            "UPDATE lookup_requests SET status='delivery_pending',found=?,result_text=?,"
            "updated_at=? WHERE id=? AND status='resolving'",
            (int(found), text, timestamp(), request_id),
        )

    async def claim_lookup_delivery(self) -> dict | None:
        async with self._lock:
            async with self._connection.execute(
                "SELECT * FROM lookup_requests WHERE status='delivery_pending' ORDER BY id LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            await self._connection.execute(
                "UPDATE lookup_requests SET status='delivering',updated_at=? "
                "WHERE id=? AND status='delivery_pending'",
                (timestamp(), row["id"]),
            )
            return dict(row)

    async def finish_lookup_delivery(self, request_id: int, delivery_status: str) -> None:
        now = timestamp()
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                async with self._connection.execute(
                    "SELECT plan_id,found,status FROM lookup_requests WHERE id=?", (request_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None or row["status"] != "delivering":
                    await self._connection.commit()
                    return
                if delivery_status == "sent" and row["found"]:
                    await self._connection.execute(
                        "INSERT OR IGNORE INTO lookup_quota_events(request_id,plan_id,created_at) "
                        "VALUES (?,?,?)",
                        (request_id, row["plan_id"], now),
                    )
                final = "sent" if delivery_status == "sent" else delivery_status
                await self._connection.execute(
                    "UPDATE lookup_requests SET status=?,phone=NULL,result_text=NULL,updated_at=? "
                    "WHERE id=?",
                    (final, now, request_id),
                )
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise

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
