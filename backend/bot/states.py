from aiogram.fsm.state import StatesGroup, State

class AddBookWizard(StatesGroup):
    author = State()      # Шаг 1
    title = State()       # Шаг 2
    photo = State()       # Шаг 3
    description = State() # Шаг 4
    confirm = State()     # Шаг 6