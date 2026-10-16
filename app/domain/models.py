from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum

TASHKENT = timezone(timedelta(hours=5), name="Asia/Tashkent")


def timestamp() -> int:
    return int(datetime.now(UTC).timestamp())


def local_date(value: int) -> str:
    return datetime.fromtimestamp(value, TASHKENT).strftime("%d.%m.%Y %H:%M")


@dataclass(frozen=True, slots=True)
class UserProfile:
    telegram_id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    joined_at: int = 0
    last_seen: int = 0
    blocked: bool = False

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)


@dataclass(frozen=True, slots=True)
class Payload:
    """Only explicitly submitted support/admin content, never lookup inputs."""

    text: str = ""
    photo_id: str | None = None

    def serialize(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def deserialize(cls, value: str) -> Payload:
        return cls(**json.loads(value))


class DeliveryStatus(StrEnum):
    SENT = "sent"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Ticket:
    id: int
    user_id: int
    status: str
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class SupportMessage:
    id: int
    ticket_id: int
    sender_id: int
    role: str
    payload: Payload
    status: str
    created_at: int


@dataclass(frozen=True, slots=True)
class Campaign:
    id: int
    admin_id: int
    payload: Payload
    status: str
    created_at: int


@dataclass(frozen=True, slots=True)
class UsageStats:
    contacts: int = 0
    phone_requests: int = 0
    user_selections: int = 0
    successful: int = 0
    unsuccessful: int = 0


@dataclass(frozen=True, slots=True)
class GlobalStats:
    total: int
    new_today: int
    active_today: int
    active_week: int
    active_month: int
    blocked: int
    usage: UsageStats
