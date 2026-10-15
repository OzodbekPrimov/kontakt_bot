import pytest

from app.domain.exceptions import InvalidPhoneNumber
from app.domain.phone_number import PhoneNumber
from app.domain.telegram_links import profile_link_for


def test_parses_and_normalizes_uzbek_phone() -> None:
    phone = PhoneNumber.parse("+998 90 123 45 67", "UZ")
    assert phone.e164 == "+998901234567"
    assert phone.masked == "+99890***4567"
    assert profile_link_for(phone) == "https://t.me/+998901234567?profile"


def test_uses_uzbekistan_as_default_region() -> None:
    phone = PhoneNumber.parse("90 123 45 67")
    assert phone.e164 == "+998901234567"


def test_rejects_invalid_phone() -> None:
    with pytest.raises(InvalidPhoneNumber):
        PhoneNumber.parse("bu-raqam-emas", "UZ")
