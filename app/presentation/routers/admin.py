from __future__ import annotations

import secrets

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.application.context import AppContext
from app.domain.models import DeliveryStatus, Payload, local_date, timestamp
from app.presentation import keyboards as kb
from app.presentation.content import payload_from
from app.presentation.formatters import registered_card, usage_card
from app.presentation.states import AdminFlow

PAGE_SIZE = 8
STATUS_NAMES = {
    "open": "Yangi / javob kutilmoqda",
    "answered": "Javob berilgan",
    "closed": "Yopilgan",
    "pending": "Kutilmoqda",
    "running": "Yuborilmoqda",
    "paused": "To‘xtatilgan",
    "completed": "Tugallangan",
    "interrupted": "Servis uzilganda to‘xtagan",
}


def create_router() -> Router:
    router = Router(name="admin")
    router.message.register(lookup_status, Command("lookup_status"), StateFilter(None))
    router.callback_query.register(admin_callback, F.data.startswith("a:"))
    router.message.register(
        admin_content,
        StateFilter(
            AdminFlow.search,
            AdminFlow.reply,
            AdminFlow.recipient,
            AdminFlow.compose,
            AdminFlow.confirm,
        ),
    )
    return router


async def lookup_status(message: Message, context: AppContext) -> None:
    if not context.is_admin(message.from_user.id):
        await message.answer("Bu amal faqat admin uchun.")
        return
    if context.lookup_worker is None:
        await message.answer("Server qidiruvi yoqilmagan.")
        return
    stats = await context.storage.lookup_status()
    pending = stats.get("pending:0", 0) + stats.get("resolving:0", 0)
    found = stats.get("sent:1", 0)
    unresolved = stats.get("sent:0", 0)
    uncertain = stats.get("unknown:0", 0) + stats.get("unknown:1", 0)
    wait = max(0, stats["next_allowed_at"] - timestamp())
    await message.answer(
        f"<b>Qidiruv holati</b>\nNavbatda: {pending}\n"
        f"Topilib yetkazilgan: {found}\n"
        f"Aniqlanmagan yoki xato: {unresolved}\n"
        f"Yetkazish holati noma’lum: {uncertain}\n"
        f"Keyingi qidiruvgacha: {wait} soniya"
    )


async def grant_lookup_plan(message: Message, context: AppContext) -> None:
    if not context.is_admin(message.from_user.id):
        await message.answer("Bu amal faqat admin uchun.")
        return
    if context.lookup_worker is None:
        await message.answer("Server qidiruvi yoqilmagan.")
        return
    parts = (message.text or "").split()
    if len(parts) != 4 or not all(part.isascii() and part.isdecimal() for part in parts[1:]):
        await message.answer(
            "Format: /lookup_plan USER_ID KONTAKT_SONI KUN. Masalan: /lookup_plan 123 300 30"
        )
        return
    user_id, capacity, days = map(int, parts[1:])
    if user_id < 1 or not 1 <= capacity <= 10000 or not 1 <= days <= 365:
        await message.answer("ID, kontakt soni yoki kunlar noto‘g‘ri.")
        return
    try:
        plan_id = await context.storage.grant_lookup_plan(
            user_id, capacity, days, message.from_user.id
        )
    except ValueError as exc:
        if str(exc) == "An active lookup plan already exists":
            await message.answer(
                "Bu foydalanuvchida faol pullik tarif bor; muddati tugagach yangisini oching."
            )
        else:
            await message.answer("Foydalanuvchi botga avval /start yuborishi kerak.")
        return
    await message.answer(f"Tarif #{plan_id} ochildi: {capacity} ta kontakt, {days} kun.")


async def statistics(message: Message, context: AppContext) -> None:
    stats = await context.storage.global_stats()
    await message.answer(
        "<b>📊 Umumiy statistika</b>\n\n"
        f"Jami foydalanuvchilar: <b>{stats.total}</b>\n"
        f"Bugungi yangi: <b>{stats.new_today}</b>\n"
        f"Bugungi faol: <b>{stats.active_today}</b>\n"
        f"Oxirgi 7 kunda faol: <b>{stats.active_week}</b>\n"
        f"Oxirgi 30 kunda faol: <b>{stats.active_month}</b>\n"
        f"Botni bloklagani ma’lum: <b>{stats.blocked}</b>\n\n"
        + usage_card(stats.usage)
        + "\n\nBugun: Toshkent vaqti. Faol — botda amal bajargan user; "
        "Telegram online holati emas. "
        "Adminlar ham jami foydalanuvchilar hisobiga kiradi.",
        reply_markup=kb.admin_menu(),
    )


def pagination(kind: str, page: int, total: int) -> list[tuple[str, str]]:
    buttons = []
    if page > 0:
        buttons.append(("⬅️ Oldingi", f"a:{kind}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        buttons.append(("Keyingi ➡️", f"a:{kind}:{page + 1}"))
    return buttons


async def users_page(message: Message, context: AppContext, page: int) -> None:
    users, total = await context.storage.list_users(page)
    rows = [
        [(f"{user.display_name[:24]} · {user.telegram_id}", f"a:user:{user.telegram_id}")]
        for user in users
    ]
    if nav := pagination("users", page, total):
        rows.append(nav)
    rows.append([("🔎 ID yoki username bo‘yicha qidirish", "a:search:0")])
    await message.answer(
        f"<b>👥 Foydalanuvchilar</b> · jami {total}\nSahifa: {page + 1}\n"
        "Kartochkani ochish uchun userni bosing.",
        reply_markup=kb.inline(rows),
    )


async def user_page(message: Message, context: AppContext, user_id: int) -> None:
    user = await context.storage.get_user(user_id)
    if user is None:
        await message.answer("Bu ID bo‘yicha bot foydalanuvchisi topilmadi.")
        return
    stats = await context.storage.user_stats(user_id)
    await message.answer(
        registered_card(user)
        + "\n\n"
        + usage_card(stats)
        + "\n\nProfil ma’lumotlari oxirgi muloqot paytidan saqlangan.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                # Registry usernames are snapshots; stable ID links avoid username reassignment.
                [kb.profile_button(None, user.telegram_id)],
                *kb.inline(
                    [[("📨 Xabar yuborish", f"a:target:{user_id}")], [("⬅️ Ro‘yxat", "a:users:0")]]
                ).inline_keyboard,
            ]
        ),
    )


async def tickets_page(message: Message, context: AppContext, page: int) -> None:
    tickets, total = await context.storage.list_tickets(page)
    rows = [
        [(f"#{t.id} · {t.user_id} · {STATUS_NAMES.get(t.status, t.status)}", f"a:ticket:{t.id}")]
        for t in tickets
    ]
    if nav := pagination("tickets", page, total):
        rows.append(nav)
    await message.answer(
        f"<b>✉️ Murojaatlar</b> · jami {total}\nSahifa: {page + 1}",
        reply_markup=kb.inline(rows) if rows else None,
    )


async def ticket_page(message: Message, context: AppContext, ticket_id: int) -> None:
    ticket = await context.storage.get_ticket(ticket_id)
    if ticket is None:
        await message.answer("Murojaat topilmadi.")
        return
    await message.answer(
        f"<b>Murojaat #{ticket.id}</b>\nUser ID: <code>{ticket.user_id}</code>\n"
        f"Holat: {STATUS_NAMES.get(ticket.status, ticket.status)}\n"
        f"Yangilangan: {local_date(ticket.updated_at)}\n\nOxirgi 5 xabar:",
    )
    for saved in await context.storage.ticket_messages(ticket_id):
        author = "Foydalanuvchi" if saved.role == "user" else "Admin"
        delivery_label = {
            "received": "Qabul qilingan",
            "sent": "Yuborilgan",
            "sending": "Yuborilmoqda",
            "blocked": "Bloklangan",
            "failed": "Xato",
            "unknown": "Yetkazilishi noma’lum",
        }.get(saved.status, "Noma’lum")
        await context.gateway.send(
            message.chat.id,
            Payload(
                f"{author} · {local_date(saved.created_at)} · {delivery_label}\n\n"
                f"{saved.payload.text}",
                saved.payload.photo_id,
            ),
        )
    await message.answer(
        "Murojaat amallari:", reply_markup=kb.ticket_buttons(ticket.id, ticket.status == "closed")
    )


async def campaigns_page(message: Message, context: AppContext) -> None:
    campaigns = await context.storage.list_campaigns()
    rows = [
        [(f"#{c.id} · {STATUS_NAMES.get(c.status, c.status)}", f"a:campaign:{c.id}")]
        for c in campaigns
    ]
    await message.answer(
        "<b>📬 Oxirgi yuborishlar</b>" if rows else "Hali yuborishlar yo‘q.",
        reply_markup=kb.inline(rows) if rows else None,
    )


async def campaign_page(message: Message, context: AppContext, campaign_id: int) -> None:
    campaign = await context.storage.get_campaign(campaign_id)
    if campaign is None:
        await message.answer("Yuborish topilmadi.")
        return
    counts = await context.storage.campaign_counts(campaign_id)
    rows = [[("🔄 Yangilash", f"a:campaign:{campaign_id}")]]
    if campaign.status == "running":
        rows.append([("⏸ To‘xtatish", f"a:pause:{campaign_id}")])
    elif campaign.status in {"paused", "interrupted", "pending"}:
        rows.append([("▶️ Qolganlarga yuborish", f"a:resume:{campaign_id}")])
    await message.answer(
        f"<b>Yuborish #{campaign_id}</b>\n"
        f"Holat: {STATUS_NAMES.get(campaign.status, campaign.status)}\n"
        f"Jami: {sum(counts.values())}\nYuborildi: {counts.get('sent', 0)}\n"
        f"Qolgan: {counts.get('pending', 0)}\nYuborilmoqda: {counts.get('sending', 0)}\n"
        f"Bloklangan: {counts.get('blocked', 0)}\nXato: {counts.get('failed', 0)}\n"
        f"Yetkazilishi noma’lum: {counts.get('unknown', 0)}\n\n"
        "Davom ettirish faqat hali yuborilmaganlarga tegishli. "
        "Noma’lum holatlar dublikat bo‘lmasligi uchun avtomatik takrorlanmaydi.",
        reply_markup=kb.inline(rows),
    )


async def begin_compose(message: Message, state: FSMContext, target_id: int | None) -> None:
    await state.clear()
    await state.set_state(AdminFlow.compose)
    await state.update_data(mode="campaign", target_id=target_id)
    target = f"ID {target_id}" if target_id is not None else "barcha foydalanuvchilar"
    await message.answer(
        f"Qabul qiluvchi: {target}.\nMatn yoki rasm yuboring. "
        "Keyingi qadamda oldindan ko‘rib tasdiqlaysiz.",
        reply_markup=kb.back_menu(),
    )


async def admin_callback(callback: CallbackQuery, state: FSMContext, context: AppContext) -> None:
    if not context.is_admin(callback.from_user.id):
        await callback.answer("Bu amal faqat admin uchun.", show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer("Admin panelni qayta oching.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Tugma eskirgan.", show_alert=True)
        return
    _, action, value = parts
    await callback.answer()
    message = callback.message
    if action in {"send", "discard"}:
        await confirm_draft(message, state, context, callback.from_user.id, action, value)
        return
    if action == "compose":
        await state.clear()
        if value == "all":
            await begin_compose(message, state, None)
        elif value == "one":
            await state.set_state(AdminFlow.recipient)
            await message.answer(
                "Bot foydalanuvchisining ID yoki @username’ini yuboring.",
                reply_markup=kb.back_menu(),
            )
        return
    try:
        number = int(value)
        if number < 0 or number > 2**63 - 1:
            raise ValueError
    except ValueError:
        await message.answer("Tugma ma’lumoti noto‘g‘ri. Admin panelni qayta oching.")
        return
    if action in {"users", "user", "tickets", "ticket", "search", "target", "reply"}:
        await state.clear()
    if action == "users":
        await users_page(message, context, number)
    elif action == "user":
        await user_page(message, context, number)
    elif action == "search":
        await state.set_state(AdminFlow.search)
        await message.answer("ID yoki @username yuboring.", reply_markup=kb.back_menu())
    elif action == "tickets":
        await tickets_page(message, context, number)
    elif action == "ticket":
        await ticket_page(message, context, number)
    elif action == "reply":
        ticket = await context.storage.get_ticket(number)
        if ticket is None or ticket.status == "closed":
            await message.answer("Murojaat yopilgan yoki topilmadi.")
            return
        await state.set_state(AdminFlow.reply)
        await state.update_data(mode="reply", ticket_id=number)
        await message.answer(
            f"Murojaat #{number} uchun javob yozing yoki rasm yuboring.",
            reply_markup=kb.back_menu(),
        )
    elif action == "close":
        ticket = await context.storage.get_ticket(number)
        if await context.storage.close_ticket(number, callback.from_user.id):
            await message.answer(f"Murojaat #{number} yopildi.")
            if ticket:
                await context.gateway.send(
                    ticket.user_id,
                    Payload(
                        f"Murojaatingiz #{number} yopildi. "
                        "Yangi savol uchun «Adminga yozish»ni bosing."
                    ),
                )
        else:
            await message.answer("Murojaat yopilgan yoki javob yuborish davom etmoqda.")
    elif action == "target":
        if await context.storage.get_user(number) is None:
            await message.answer("User topilmadi. Faqat bot foydalanuvchisiga yuborish mumkin.")
            return
        await begin_compose(message, state, number)
    elif action == "campaign":
        await campaign_page(message, context, number)
    elif action == "pause":
        await context.messaging.pause(callback.from_user.id, number)
        await message.answer("Yuborishni to‘xtatish so‘raldi. Joriy xabar yakunlanishi mumkin.")
        await campaign_page(message, context, number)
    elif action == "resume":
        started = await context.messaging.start(callback.from_user.id, number)
        await message.answer(
            "Yuborish davom ettirildi."
            if started
            else "Yuborish boshlangan yoki boshqa yuborish hali ishlayapti."
        )
        await campaign_page(message, context, number)


async def admin_content(message: Message, state: FSMContext, context: AppContext) -> None:
    if not context.is_admin(message.from_user.id):
        await state.clear()
        await message.answer("Bu amal faqat admin uchun.", reply_markup=kb.main_menu())
        return
    current = await state.get_state()
    if current in {AdminFlow.search.state, AdminFlow.recipient.state}:
        query = (message.text or "").strip()
        if not query or len(query) > 64:
            await message.answer("Telegram ID yoki @username yuboring.")
            return
        users, _ = await context.storage.list_users(0, query)
        if len(users) != 1:
            await message.answer(
                "Bot foydalanuvchisi topilmadi yoki username eskirgan. ID bilan tekshiring."
            )
            return
        await state.clear()
        if current == AdminFlow.search.state:
            await user_page(message, context, users[0].telegram_id)
        else:
            await begin_compose(message, state, users[0].telegram_id)
        return
    if current == AdminFlow.confirm.state:
        await message.answer(
            "Oldindan ko‘rishdagi «Yuborish» yoki «Bekor qilish» tugmasini bosing."
        )
        return
    payload = payload_from(message)
    if payload is None:
        await message.answer("Matn yoki bitta rasm yuboring.")
        return
    data = await state.get_data()
    if await context.gateway.send(message.chat.id, payload) != DeliveryStatus.SENT:
        await message.answer("Oldindan ko‘rish yuborilmadi. Qayta urinib ko‘ring.")
        return
    nonce = secrets.token_hex(6)
    await state.update_data(
        payload=payload.serialize(),
        nonce=nonce,
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await state.set_state(AdminFlow.confirm)
    if data.get("mode") == "reply":
        target = f"Murojaat #{data['ticket_id']} egasiga"
    elif data.get("target_id") is not None:
        target = f"ID {data['target_id']} ga"
    else:
        recipients = await context.storage.recipient_ids()
        await state.update_data(recipient_ids=recipients)
        target = f"{len(recipients)} ta foydalanuvchiga"
    await message.answer(
        f"<b>Oldindan ko‘rish</b>\nYuqoridagi xabar {target} yuboriladi. Tasdiqlaysizmi?",
        reply_markup=kb.inline(
            [
                [
                    ("✅ Yuborish", f"a:send:{nonce}"),
                    ("❌ Bekor qilish", f"a:discard:{nonce}"),
                ]
            ]
        ),
    )


async def confirm_draft(
    message: Message,
    state: FSMContext,
    context: AppContext,
    admin_id: int,
    action: str,
    nonce: str,
) -> None:
    data = await state.get_data()
    if await state.get_state() != AdminFlow.confirm.state or data.get("nonce") != nonce:
        await message.answer("Bu tasdiqlash tugmasi eskirgan yoki ishlatilgan.")
        return
    # Consume the exact draft once. A stale confirmation cannot send a newer draft.
    await state.clear()
    if action == "discard":
        await message.answer("Yuborish bekor qilindi.", reply_markup=kb.admin_menu())
        return
    payload = Payload.deserialize(data["payload"])
    if data["mode"] == "reply":
        status = await context.support.reply(
            admin_id, data["ticket_id"], data["source_chat_id"], data["source_message_id"], payload
        )
        result = {
            DeliveryStatus.SENT: "✅ Javob yuborildi.",
            DeliveryStatus.BLOCKED: "User botni bloklagan; javob yuborilmadi.",
            DeliveryStatus.FAILED: "Telegram javobni qabul qilmadi. Murojaat tarixidan tekshiring.",
            DeliveryStatus.UNKNOWN: (
                "Yetkazilgani noma’lum. Dublikat bo‘lmasligi uchun takrorlanmadi."
            ),
            None: "Murojaat yopilgan, xabar takroriy yoki boshqa javob yuborilmoqda.",
        }[status]
        await message.answer(result, reply_markup=kb.admin_menu())
    else:
        campaign = await context.messaging.create(
            admin_id, payload, data.get("target_id"), data.get("recipient_ids")
        )
        started = await context.messaging.start(admin_id, campaign.id)
        status_text = "boshlandi" if started else "saqlandi; Yuborishlar orqali boshlang"
        await message.answer(
            f"Yuborish #{campaign.id} {status_text}.",
            reply_markup=kb.admin_menu(),
        )
        await campaign_page(message, context, campaign.id)
