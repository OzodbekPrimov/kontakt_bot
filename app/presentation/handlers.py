from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.application.services import PhoneLookupService
from app.domain.exceptions import InvalidPhoneNumber
from app.presentation.texts import PRIVACY, START

router = Router(name=__name__)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(START)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Raqam yoki kontakt yuboring, masalan: <code>+998901234567</code>\n"
        "/help · /privacy · /myprofile"
    )


@router.message(Command("privacy"))
async def privacy(message: Message) -> None:
    await message.answer(PRIVACY)


@router.message(Command("myprofile"))
async def my_profile(message: Message) -> None:
    username = message.from_user.username if message.from_user else None
    if not username:
        await message.answer(
            "Sizda public username yo‘q. Telegram Settings → Username orqali o‘rnating."
        )
        return
    await message.answer(f"Sizning profilingiz: https://t.me/{username}")


@router.message(F.contact)
@router.message(F.text & ~F.text.startswith("/"))
async def lookup_phone(
    message: Message,
    lookup_service: PhoneLookupService,
) -> None:
    raw_phone = message.contact.phone_number if message.contact else message.text or ""
    try:
        result = lookup_service.create_link(raw_phone)
    except InvalidPhoneNumber:
        await message.answer("Raqam noto‘g‘ri. Masalan: <code>+998901234567</code>")
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Telegram’da profilni ochish", url=result.profile_url)]
            ]
        )
        await message.answer(
            f"{result.masked_phone} uchun havola tayyor. Telegram profil/chatni ochishi "
            "sizning maxfiylik ruxsatlaringizga bog‘liq.",
            reply_markup=keyboard,
        )
