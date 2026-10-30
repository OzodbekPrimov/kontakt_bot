from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

from app.application.context import AppContext
from app.domain.models import UserProfile
from app.presentation import keyboards as kb
from app.presentation.content import sender_profile
from app.presentation.formatters import usage_card, user_card
from app.presentation.states import UserFlow
from app.presentation.texts import PRIVACY, SERVER_PRIVACY, SERVER_START, START


def create_router() -> Router:
    router = Router(name="navigation")
    router.message.register(
        navigation,
        Command(
            "start",
            "help",
            "privacy",
            "myprofile",
            "myid",
            "stats",
            "admin",
            "cancel",
            "lookup_balance",
        ),
    )
    router.message.register(navigation, F.text.in_(kb.NAVIGATION))
    router.message.register(shared_user, F.users_shared)
    return router


async def navigation(message: Message, state: FSMContext, context: AppContext) -> None:
    # Navigation wins over conversation states: menu text is never relayed to the admin.
    previous_state = await state.get_state()
    await state.clear()
    text = message.text or ""
    action = text.split()[0].split("@")[0].lower() if text.startswith("/") else text
    is_admin = context.is_admin(message.from_user.id)
    menu = kb.main_menu(is_admin)
    if action == "/lookup_balance":
        if context.lookup_worker is None:
            await message.answer("Server qidiruvi hozir yoqilmagan.")
            return
        balance = await context.storage.lookup_balance(message.from_user.id)
        if balance is None:
            if context.lookup_worker.free_limit:
                await message.answer("Faol tarif yo‘q. Birinchi qidiruvda bepul tarif ochiladi.")
            else:
                await message.answer("Faol tarif yo‘q. Admin bilan bog‘laning.")
        else:
            from app.domain.models import local_date

            capacity, available, expires_at = balance
            await message.answer(
                f"Kontaktlar: {available}/{capacity} mavjud. "
                f"Tarif muddati: {local_date(expires_at)}."
            )
        return
    if (
        action == kb.BACK
        and is_admin
        and previous_state
        and previous_state.startswith("AdminFlow:")
    ):
        await message.answer("Admin panelga qaytdingiz.", reply_markup=kb.admin_menu())
        return
    if action in {"/admin", kb.ADMIN, kb.STATS, kb.USERS, kb.TICKETS, kb.COMPOSE, kb.CAMPAIGNS}:
        if not is_admin:
            await message.answer("Bu bo‘lim faqat admin uchun.", reply_markup=menu)
            return
        from app.presentation.routers import admin

        if action == kb.STATS:
            await admin.statistics(message, context)
        elif action == kb.USERS:
            await admin.users_page(message, context, 0)
        elif action == kb.TICKETS:
            await admin.tickets_page(message, context, 0)
        elif action == kb.COMPOSE:
            await message.answer(
                "Kimga yuborasiz?",
                reply_markup=kb.inline(
                    [
                        [("👤 Bitta foydalanuvchiga", "a:compose:one")],
                        [("📢 Barcha foydalanuvchilarga", "a:compose:all")],
                    ]
                ),
            )
        elif action == kb.CAMPAIGNS:
            await admin.campaigns_page(message, context)
        else:
            await message.answer(
                "<b>Admin panel</b>\nAmalni tanlang.", reply_markup=kb.admin_menu()
            )
        return
    if action in {kb.MY_ID, "/myid", "/myprofile"}:
        profile = sender_profile(message)
        await message.answer(
            user_card(profile),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[kb.profile_button(profile.username, profile.telegram_id)]]
            ),
        )
    elif action in {kb.MY_STATS, "/stats"}:
        await message.answer(
            "<b>Sizning statistikangiz</b>\n\n"
            + usage_card(await context.storage.user_stats(message.from_user.id)),
            reply_markup=menu,
        )
    elif action == kb.PHONE:
        await message.answer(
            "Raqam yoki kontakt yuboring. Masalan: <code>+998901234567</code>\n"
            "O‘z raqamingizni tugma orqali ham ulashishingiz mumkin.",
            reply_markup=kb.back_menu(own_contact=True),
        )
    elif action == kb.SUPPORT:
        if not context.admin_ids:
            await message.answer(
                "Admin hali sozlanmagan. Keyinroq urinib ko‘ring.", reply_markup=menu
            )
            return
        await state.set_state(UserFlow.support)
        await message.answer(
            "Adminga yuboriladigan xabaringizni yozing yoki rasm yuboring.\n"
            "Xabar va profil nomingiz adminlarga ko‘rsatiladi. Chiqish uchun Bosh menyu.",
            reply_markup=kb.back_menu(),
        )
    elif action in {kb.HELP, "/help"}:
        lookup_help = (
            "📱 Raqam/kontakt → Telegram’da tekshiruv va natija. /lookup_balance → qoldiq.\n"
            if context.lookup_worker is not None
            else "📱 Raqam/kontakt → texnik ma’lumot va Telegram havolasi.\n"
        )
        await message.answer(
            lookup_help + "🆔 Mening ID’im → o‘z profil ma’lumotlaringiz.\n"
            "👤 Boshqa user ID → Telegram oynasidan user tanlash.\n"
            "✉️ Adminga yozish → murojaat va javoblar.\n\n"
            "Profil ochilishi Telegram ruxsatlariga bog‘liq.\n"
            "/privacy — saqlanadigan ma’lumotlar. /cancel — amalni bekor qilish.",
            reply_markup=menu,
        )
    elif action == "/privacy":
        await message.answer(
            SERVER_PRIVACY if context.lookup_worker is not None else PRIVACY,
            reply_markup=menu,
        )
    elif action == kb.OTHER_ID:
        await message.answer(
            "Pastdagi «Boshqa user ID» tugmasidan user tanlang.", reply_markup=menu
        )
    else:
        await message.answer(
            (SERVER_START if context.lookup_worker is not None else START)
            if action == "/start"
            else "Bosh menyu. Amalni tanlang.",
            reply_markup=menu,
        )


async def shared_user(message: Message, state: FSMContext, context: AppContext) -> None:
    await state.clear()
    shared = message.users_shared
    if shared.request_id != kb.USER_REQUEST_ID or len(shared.users) != 1:
        await message.answer(
            "Userni menyudagi tugma orqali qayta tanlang.",
            reply_markup=kb.main_menu(context.is_admin(message.from_user.id)),
        )
        return
    selected = shared.users[0]
    profile = UserProfile(
        selected.user_id,
        selected.first_name or "Tanlangan foydalanuvchi",
        selected.last_name,
        selected.username,
    )
    await context.storage.record_usage(
        message.from_user.id, message.chat.id, message.message_id, "selection", True
    )
    await message.answer(
        "<b>Tanlangan foydalanuvchi</b>\n"
        + user_card(profile, missing_username="Telegram qaytarmadi")
        + "\n\nMa’lumot manbasi: Telegram user tanlash oynasi. "
        "ID borligi bot bu odamga xabar yubora olishini bildirmaydi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[kb.profile_button(profile.username, profile.telegram_id)]]
        )
        if profile.username
        else None,
    )
