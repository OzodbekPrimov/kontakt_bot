from __future__ import annotations

from dataclasses import dataclass

import phonenumbers
from phonenumbers import PhoneNumberType, carrier, timezone

from app.domain.phone_number import PhoneNumber
from app.domain.telegram_links import profile_link_for


@dataclass(frozen=True, slots=True)
class LookupResult:
    profile_url: str
    masked_phone: str
    international: str
    region: str
    country_code: int
    number_type: str
    original_carrier: str
    time_zones: tuple[str, ...]


class PhoneLookupService:
    """Creates client-side Telegram deep links without querying a Telegram account."""

    def create_link(self, raw_phone: str) -> LookupResult:
        phone = PhoneNumber.parse(raw_phone)
        parsed = phonenumbers.parse(phone.e164, None)
        types = {
            PhoneNumberType.MOBILE: "Mobil",
            PhoneNumberType.FIXED_LINE: "Shahar telefoni",
            PhoneNumberType.FIXED_LINE_OR_MOBILE: "Shahar yoki mobil",
            PhoneNumberType.VOIP: "VoIP",
            PhoneNumberType.TOLL_FREE: "Bepul xizmat raqami",
            PhoneNumberType.PREMIUM_RATE: "Pullik xizmat raqami",
        }
        zones = tuple(z for z in timezone.time_zones_for_number(parsed) if z != "Etc/Unknown")
        region = phonenumbers.region_code_for_number(parsed) or "Noma’lum"
        return LookupResult(
            profile_url=profile_link_for(phone),
            masked_phone=phone.masked,
            international=phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            ),
            region="O‘zbekiston (UZ)" if region == "UZ" else region,
            country_code=parsed.country_code,
            number_type=types.get(phonenumbers.number_type(parsed), "Boshqa / noma’lum"),
            original_carrier=carrier.name_for_number(parsed, "en") or "Ma’lumot yo‘q",
            time_zones=zones,
        )
