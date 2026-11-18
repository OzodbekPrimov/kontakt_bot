from __future__ import annotations

from dataclasses import dataclass

import phonenumbers
from phonenumbers import PhoneNumberFormat

from app.domain.exceptions import InvalidPhoneNumber


@dataclass(frozen=True, slots=True)
class PhoneNumber:
    """A valid E.164 phone number. Its raw input must never be persisted."""

    e164: str

    @property
    def digits(self) -> str:
        return self.e164.removeprefix("+")

    @property
    def masked(self) -> str:
        if len(self.digits) < 7:
            return "***"
        return f"+{self.digits[:5]}***{self.digits[-4:]}"

    @classmethod
    def parse(cls, value: str, default_region: str = "UZ") -> PhoneNumber:
        try:
            parsed = phonenumbers.parse(value.strip(), default_region.upper())
        except phonenumbers.NumberParseException as exc:
            raise InvalidPhoneNumber("Invalid phone number") from exc
        if not phonenumbers.is_valid_number(parsed):
            raise InvalidPhoneNumber("Invalid phone number")
        return cls(phonenumbers.format_number(parsed, PhoneNumberFormat.E164))

    @classmethod
    def from_contact(cls, value: str, default_region: str = "UZ") -> PhoneNumber:
        """Telegram contacts carry international numbers, usually without the leading '+'.

        Parsing them against the default region would reject every foreign contact,
        so the international reading wins; a locally saved number falls back to the region.
        """
        value = value.strip()
        if value.startswith("+"):
            return cls.parse(value, default_region)
        try:
            return cls.parse(f"+{value}", default_region)
        except InvalidPhoneNumber:
            return cls.parse(value, default_region)
