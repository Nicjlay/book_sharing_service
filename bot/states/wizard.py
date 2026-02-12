"""
FSM States для пошаговых диалогов (визардов)
"""
from aiogram.fsm.state import State, StatesGroup


class AddBookWizard(StatesGroup):
    """Визард добавления книги (ТЗ 3.1)"""
    author = State()          # Шаг 1: Ввод автора
    title = State()           # Шаг 2: Ввод названия
    photo = State()           # Шаг 3: Загрузка фото (опционально)
    description = State()     # Шаг 4: Описание и выбор жанра
    owner = State()           # Шаг 5: Выбор владельца
    confirm = State()         # Шаг 6: Подтверждение


class EditBookStates(StatesGroup):
    """Редактирование книги (ТЗ 4.1)"""
    select_field = State()    # Выбор поля для редактирования
    edit_author = State()
    edit_title = State()
    edit_photo = State()
    edit_description = State()
    edit_genre = State()


class ReservationStates(StatesGroup):
    """Бронирование книги"""
    select_days = State()     # Выбор срока бронирования
    custom_days = State()     # Ввод произвольного срока


class ReturnBookStates(StatesGroup):
    """Возврат книги"""
    upload_photo = State()    # Загрузка фото книги


class SearchStates(StatesGroup):
    """Поиск книг"""
    query = State()           # Ввод поискового запроса


class AdminRejectStates(StatesGroup):
    """Отклонение брони админом"""
    reason = State()          # Ввод причины отклонения


class AdminApproveStates(StatesGroup):
    """Подтверждение брони админом"""
    due_date = State()        # Выбор даты возврата
