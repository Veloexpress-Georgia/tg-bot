from aiogram.fsm.state import State, StatesGroup


class ExtraDayStates(StatesGroup):
    editing = State()
