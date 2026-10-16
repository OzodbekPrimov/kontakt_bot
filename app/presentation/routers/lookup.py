from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.application.context import AppContext
from app.domain.exceptions import InvalidPhoneNumber


def create_router() -> Router:
    router = Router(name="lookup")
    router.message.register(lookup, StateFilter(None), F.contact)
    router.message.register(lookup, StateFilter(None), F.text & ~F.text.startswith("/"))
    router.message.register(fallback)
    return router


async def lookup(message: Message, context: AppContext) -> None:
    contact = message.contact
    raw_phone = contact.phone_number if contact else message.text or ""
    kind = "contact" if contact else "phone"
    try:
        result = context.lookup.create_link(raw_phone)
    except InvalidPhoneNumber:
        await context.storage.record_usage(
            message.from_user.id, message.chat.id, message.message_id, kind, False
        )
        await message.answer("Raqam formati noto‘g‘ri. Masalan: <code>+998901234567</code>")
        return
    await context.storage.record_usage(
        message.from_user.id, message.chat.id, message.message_id, kind, True
    )
    telegram_id = f"<code>{contact.user_id}</code>" if contact and contact.user_id else "Berilmagan"
    # Only the sender's own shared contact can be linked to their User object's username.
    own = contact is not None and contact.user_id == message.from_user.id
    username = (
        f"@{escape(message.from_user.username)}"
        if own and message.from_user.username
        else ("O‘rnatilmagan" if own else "Bu raqamdan aniqlanmaydi")
    )
    name = ""
    if contact:
        supplied = " ".join(part for part in (contact.first_name, contact.last_name) if part)
        name = f"Kontaktdagi nom: {escape(supplied)}\n"
    await message.answer(
        f"<b>📱 Raqam ma’lumoti</b>\n\n{name}"
        f"Raqam: <code>{escape(result.international)}</code>\n"
        f"Davlat/hudud: {escape(result.region)}\n"
        f"Davlat kodi: +{result.country_code}\n"
        f"Raqam turi: {escape(result.number_type)}\n"
        f"Dastlabki operator: {escape(result.original_carrier)}\n"
        f"Raqam hududi vaqt zonasi: {escape(', '.join(result.time_zones)) or 'Ma’lumot yo‘q'}\n"
        f"Telegram ID: {telegram_id}\nUsername: {username}\n\n"
        "Format raqamlash qoidalariga mos. Bu SIM faol yoki Telegram’da borligini tasdiqlamaydi. "
        "Operator o‘zgargan bo‘lishi mumkin; vaqt zonasi hozirgi joylashuvni bildirmaydi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Telegram’da profilni ochish", url=result.profile_url)]
            ]
        ),
    )


async def fallback(message: Message) -> None:
    await message.answer("Pastdagi menyudan amal tanlang yoki raqam/kontakt yuboring.")
