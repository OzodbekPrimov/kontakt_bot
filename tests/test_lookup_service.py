import pytest

from app.application.services import PhoneLookupService
from app.domain.exceptions import InvalidPhoneNumber


def test_service_returns_a_deep_link_without_contacting_telegram() -> None:
    service = PhoneLookupService()
    result = service.create_link("+998901234567")
    assert result.profile_url == "https://t.me/+998901234567?profile"
    assert result.masked_phone == "+99890***4567"


def test_repeated_requests_have_no_application_quota() -> None:
    service = PhoneLookupService()
    for _ in range(150):
        assert service.create_link("+998901234567").profile_url.endswith("?profile")


def test_service_rejects_invalid_phone() -> None:
    with pytest.raises(InvalidPhoneNumber):
        PhoneLookupService().create_link("invalid")
