from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.application.context import AppContext
from app.presentation import keyboards as kb
from app.presentation.content import payload_from
from app.presentation.states import UserFlow


def create_router() -> Router:
    router = Router(name="support")
    router.message.register(submit, UserFlow.support)
    return router


async def submit(message: Message, state: FSMContext, context: AppContext) -> None:
    payload = payload_from(message)
    if payload is None:
        await message.answer("Murojaat uchun matn yoki bitta rasm yuboring. Chiqish: Bosh menyu.")
        return
    ticket, created = await context.support.submit(
        message.from_user.id, message.chat.id, message.message_id, payload
    )
    await state.clear()
    delivered = False
    if created:
        for admin_id in context.admin_ids:
            # Notifications contain a ticket link; only authorized admins can open its contents.
            try:
                await message.bot.send_message(
                    admin_id,
                    f"<b>Yangi xabar · Murojaat #{ticket.id}</b>\n"
                    f"Kimdan: {escape(message.from_user.full_name)}\n"
                    f"Telegram ID: <code>{ticket.user_id}</code>",
                    reply_markup=kb.ticket_buttons(ticket.id),
                )
                delivered = True
            except TelegramAPIError:
                continue
    text = f"✅ Murojaatingiz #{ticket.id} saqlandi. Admin javobi shu botga keladi."
    if created and not delivered:
        text += "\nAdmin hozir xabardor qilinmadi, murojaatingiz panelda saqlangan."
    await message.answer(text, reply_markup=kb.main_menu(context.is_admin(message.from_user.id)))
