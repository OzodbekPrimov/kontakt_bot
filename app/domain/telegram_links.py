from app.domain.phone_number import PhoneNumber


def profile_link_for(phone: PhoneNumber) -> str:
    """Build a Telegram phone deep link; this does not reveal account existence."""
    return f"https://t.me/+{phone.digits}?profile"
