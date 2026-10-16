from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    ReplyKeyboardMarkup,
)

PHONE = "📱 Raqam ma’lumoti"
MY_ID = "🆔 Mening ID’im"
OTHER_ID = "👤 Boshqa user ID"
MY_STATS = "📊 Mening statistikam"
SUPPORT = "✉️ Adminga yozish"
HELP = "ℹ️ Yordam"
HOME = "🏠 Bosh menyu"
BACK = "⬅️ Orqaga"
CANCEL = "❌ Bekor qilish"
ADMIN = "⚙️ Admin panel"
STATS = "📊 Umumiy statistika"
USERS = "👥 Foydalanuvchilar"
TICKETS = "✉️ Murojaatlar"
COMPOSE = "📨 Xabar yuborish"
CAMPAIGNS = "📬 Yuborishlar"
OWN_CONTACT = "📲 O‘z raqamimni ulashish"
USER_REQUEST_ID = 43101

NAVIGATION = {
    PHONE,
    MY_ID,
    OTHER_ID,
    MY_STATS,
    SUPPORT,
    HELP,
    HOME,
    BACK,
    CANCEL,
    ADMIN,
    STATS,
    USERS,
    TICKETS,
    COMPOSE,
    CAMPAIGNS,
}


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=PHONE), KeyboardButton(text=MY_ID)],
        [
            KeyboardButton(
                text=OTHER_ID,
                request_users=KeyboardButtonRequestUsers(
                    request_id=USER_REQUEST_ID,
                    user_is_bot=False,
                    max_quantity=1,
                    request_name=True,
                    request_username=True,
                ),
            ),
            KeyboardButton(text=MY_STATS),
        ],
        [KeyboardButton(text=SUPPORT), KeyboardButton(text=HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=STATS), KeyboardButton(text=USERS)],
            [KeyboardButton(text=TICKETS), KeyboardButton(text=COMPOSE)],
            [KeyboardButton(text=CAMPAIGNS), KeyboardButton(text=HOME)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def back_menu(*, own_contact: bool = False) -> ReplyKeyboardMarkup:
    rows = []
    if own_contact:
        rows.append([KeyboardButton(text=OWN_CONTACT, request_contact=True)])
    rows.append([KeyboardButton(text=BACK), KeyboardButton(text=HOME)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def profile_button(username: str | None, user_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="👤 Profilni ochish",
        url=f"https://t.me/{username}" if username else f"tg://user?id={user_id}",
    )


def ticket_buttons(ticket_id: int, closed: bool = False) -> InlineKeyboardMarkup:
    rows = [[("🔎 Murojaatni ochish", f"a:ticket:{ticket_id}")]]
    if not closed:
        rows.append(
            [("✍️ Javob berish", f"a:reply:{ticket_id}"), ("✅ Yopish", f"a:close:{ticket_id}")]
        )
    return inline(rows)
