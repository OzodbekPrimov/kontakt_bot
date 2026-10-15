from __future__ import annotations

from dataclasses import dataclass

from app.domain.phone_number import PhoneNumber
from app.domain.telegram_links import profile_link_for


@dataclass(frozen=True, slots=True)
class LookupResult:
    profile_url: str
    masked_phone: str


class PhoneLookupService:
    """Creates client-side Telegram deep links without querying a Telegram account."""

    def create_link(self, raw_phone: str) -> LookupResult:
        phone = PhoneNumber.parse(raw_phone)
        return LookupResult(profile_url=profile_link_for(phone), masked_phone=phone.masked)
