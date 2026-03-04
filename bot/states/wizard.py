"""
FSM States для пошаговых диалогов (визардов)
"""
from aiogram.fsm.state import State, StatesGroup


class AddBookWizard(StatesGroup):
    """Визард добавления книги (ТЗ 3.1)"""
    author = State()
    title = State()
    photo = State()
    description = State()
    owner = State()
    confirm = State()


class EditBookStates(StatesGroup):
    """Редактирование книги (ТЗ 4.1)"""
    select_field = State()
    edit_author = State()
    edit_title = State()
    edit_photo = State()
    edit_description = State()
    edit_genre = State()


class ReservationStates(StatesGroup):
    """Бронирование книги"""
    select_days = State()
    custom_days = State()


class ReturnBookStates(StatesGroup):
    """Возврат книги"""
    upload_photo = State()


class SearchStates(StatesGroup):
    """Поиск книг"""
    query = State()


class AdminRejectStates(StatesGroup):
    """Отклонение брони админом"""
    reason = State()


class AdminApproveStates(StatesGroup):
    """Подтверждение брони админом"""
    due_date = State()