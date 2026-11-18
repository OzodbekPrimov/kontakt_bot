from app.application.services import PhoneLookupService
from app.domain.phone_number import PhoneNumber

PHONE = PhoneNumber.parse("+998901234567")


def test_service_returns_a_deep_link_without_contacting_telegram() -> None:
    result = PhoneLookupService().create_link(PHONE)
    assert result.profile_url == "https://t.me/+998901234567?profile"
    assert result.masked_phone == "+99890***4567"


def test_repeated_requests_have_no_application_quota() -> None:
    service = PhoneLookupService()
    for _ in range(150):
        assert service.create_link(PHONE).profile_url.endswith("?profile")
