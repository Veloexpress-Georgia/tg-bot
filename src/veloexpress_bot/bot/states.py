from aiogram.fsm.state import State, StatesGroup


class PollSetupStates(StatesGroup):
    editing = State()
