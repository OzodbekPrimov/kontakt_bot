from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.application.context import AppContext
from app.domain.exceptions import InvalidPhoneNumber
from app.domain.models import LOOKUP_INTERVAL_SECONDS
from app.domain.phone_number import PhoneNumber
from app.presentation import keyboards as kb

INVALID_FORMAT = "Raqam formati noto‘g‘ri. Masalan: <code>+998901234567</code>"
QUEUE_REPLIES = {
    "queued": "So‘rov #{id} navbatga olindi. Natija shu chatga keladi.",
    "duplicate": "So‘rov #{id} avval qabul qilingan.",
    "busy": "Sizda {limit} ta tekshiruv navbatda turibdi. Natijalar kelgach yangisini yuboring.",
    "exhausted": "Kontakt limiti tugagan. Admin bilan bog‘laning.",
    "no_plan": "Faol kontakt tarifi yo‘q. Admin bilan bog‘laning.",
}


def create_router() -> Router:
    router = Router(name="lookup")
    router.message.register(lookup, StateFilter(None), F.contact)
    router.message.register(lookup, StateFilter(None), F.text & ~F.text.startswith("/"))
    router.message.register(fallback)
    return router


def parse_phone(message: Message) -> PhoneNumber | None:
    try:
        if message.contact:
            return PhoneNumber.from_contact(message.contact.phone_number)
        return PhoneNumber.parse(message.text or "")
    except InvalidPhoneNumber:
        return None


async def lookup(message: Message, context: AppContext) -> None:
    phone = parse_phone(message)
    await context.storage.record_usage(
        message.from_user.id,
        message.chat.id,
        message.message_id,
        "contact" if message.contact else "phone",
        phone is not None,
    )
    if phone is None:
        await message.answer(INVALID_FORMAT)
    elif context.lookup_worker is None:
        await answer_phone_card(message, context, phone)
    elif message.contact and message.contact.user_id:
        # Telegram already attached the account to the contact: no rate-limited lookup needed.
        await answer_known_contact(message, phone)
    else:
        await enqueue_server_lookup(message, context, phone)


async def answer_known_contact(message: Message, phone: PhoneNumber) -> None:
    contact = message.contact
    supplied = " ".join(part for part in (contact.first_name, contact.last_name) if part)
    own = contact.user_id == message.from_user.id
    username = message.from_user.username if own else None
    await message.answer(
        "<b>Telegram profili aniqlandi</b>\n"
        f"Raqam: {escape(phone.masked)}\n"
        f"Kontaktdagi nom: {escape(supplied) or 'Ko‘rsatilmagan'}\n"
        f"ID: <code>{contact.user_id}</code>\n"
        f"Username: {f'@{escape(username)}' if username else 'Kontaktdan aniqlanmaydi'}\n\n"
        "Manba: Telegram kontakt kartasi. Navbat va limit ishlatilmadi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[kb.profile_button(username, contact.user_id)]]
        ),
    )


async def enqueue_server_lookup(message: Message, context: AppContext, phone: PhoneNumber) -> None:
    worker = context.lookup_worker
    request_id, outcome = await context.storage.enqueue_lookup(
        message.from_user.id,
        message.chat.id,
        message.message_id,
        phone.e164,
        phone.masked,
        worker.free_limit,
        worker.open_access,
        max_active=worker.max_active_per_user,
    )
    text = QUEUE_REPLIES[outcome].format(id=request_id, limit=worker.max_active_per_user)
    if outcome == "queued":
        ahead = await context.storage.active_lookups()
        text += f"\nNavbatda: {ahead} ta. Taxminiy kutish: ~{ahead * LOOKUP_INTERVAL_SECONDS} s."
    await message.answer(text)


async def answer_phone_card(message: Message, context: AppContext, phone: PhoneNumber) -> None:
    result = context.lookup.create_link(phone)
    contact = message.contact
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
