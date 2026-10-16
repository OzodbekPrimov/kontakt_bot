from aiogram.fsm.state import State, StatesGroup


class UserFlow(StatesGroup):
    support = State()


class AdminFlow(StatesGroup):
    search = State()
    reply = State()
    recipient = State()
    compose = State()
    confirm = State()
