from html import escape

from app.domain.models import UsageStats, UserProfile, local_date


def user_card(profile: UserProfile, missing_username: str = "O‘rnatilmagan") -> str:
    username = f"@{escape(profile.username)}" if profile.username else escape(missing_username)
    return (
        f"<b>{escape(profile.display_name)}</b>\n"
        f"Telegram ID: <code>{profile.telegram_id}</code>\n"
        f"Username: {username}"
    )


def usage_card(stats: UsageStats) -> str:
    return (
        f"Kontaktlar: <b>{stats.contacts}</b>\n"
        f"Raqamli so‘rovlar: <b>{stats.phone_requests}</b>\n"
        f"User tanlash: <b>{stats.user_selections}</b>\n"
        f"Muvaffaqiyatli: <b>{stats.successful}</b>\n"
        f"Noto‘g‘ri / ma’lumotsiz: <b>{stats.unsuccessful}</b>"
    )


def registered_card(profile: UserProfile) -> str:
    return (
        f"{user_card(profile)}\n\n"
        f"Qo‘shilgan: {local_date(profile.joined_at)}\n"
        f"Oxirgi faollik: {local_date(profile.last_seen)}\n"
        f"Botni bloklagan: {'Ha' if profile.blocked else 'Yo‘q / noma’lum'}"
    )
