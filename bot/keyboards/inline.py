"""
Inline клавиатуры для бота
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📖 Каталог", callback_data="catalog"),
        InlineKeyboardButton(text="🔍 Поиск", callback_data="search")
    )
    builder.row(
        InlineKeyboardButton(text="📚 Мои книги", callback_data="my_books")
    )
    
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="👨‍💼 Админ панель", callback_data="admin_panel")
        )
    
    return builder.as_markup()


def catalog_filters_keyboard() -> InlineKeyboardMarkup:
    """Фильтры каталога (ТЗ 3.2.1)"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📚 Все книги", callback_data="filter:all"),
        InlineKeyboardButton(text="🟢 Доступные", callback_data="filter:available")
    )
    builder.row(
        InlineKeyboardButton(text="📖 По жанрам", callback_data="filter:genres")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")
    )
    
    return builder.as_markup()


def genres_keyboard(genres: List[str]) -> InlineKeyboardMarkup:
    """Выбор жанра"""
    builder = InlineKeyboardBuilder()
    
    for genre in genres:
        builder.row(
            InlineKeyboardButton(text=genre, callback_data=f"genre:{genre}")
        )
    
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="catalog")
    )
    
    return builder.as_markup()


def book_card_keyboard(book: Dict, user_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Карточка книги с действиями"""
    builder = InlineKeyboardBuilder()
    
    status = book.get("status")
    owner_id = book.get("owner_id")
    borrower_id = book.get("borrower_id")
    
    # Действия в зависимости от статуса и прав
    if status == "available":
        # Книга свободна
        if owner_id != user_id:
            builder.row(
                InlineKeyboardButton(text="🟢 Забронировать", callback_data=f"reserve:{book['id']}")
            )
    
    elif status == "reserved":
        # Книга забронирована (ждет админа)
        if borrower_id == user_id:
            builder.row(
                InlineKeyboardButton(text="⏳ Ожидает подтверждения", callback_data="noop")
            )
        elif is_admin:
            builder.row(
                InlineKeyboardButton(text="✅ Подтвердить выдачу", callback_data=f"approve:{book['id']}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{book['id']}")
            )
    
    elif status in ["borrowed", "overdue"]:
        # Книга выдана
        if borrower_id == user_id or owner_id == user_id or is_admin:
            builder.row(
                InlineKeyboardButton(text="🔙 Вернуть книгу", callback_data=f"return:{book['id']}")
            )
        else:
            builder.row(
                InlineKeyboardButton(text="🔔 Уведомить меня", callback_data=f"waitlist:{book['id']}")
            )
    
    # Редактирование (только владелец)
    if owner_id == user_id and status == "available":
        builder.row(
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{book['id']}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{book['id']}")
        )
    
    # История книги
    builder.row(
        InlineKeyboardButton(text="📜 История", callback_data=f"history:{book['id']}")
    )
    
    # Назад
    builder.row(
        InlineKeyboardButton(text="🔙 Назад к каталогу", callback_data="catalog")
    )
    
    return builder.as_markup()


def reservation_days_keyboard() -> InlineKeyboardMarkup:
    """Выбор срока бронирования (ТЗ 3.2.2)"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📅 +1 неделя", callback_data="days:7"),
        InlineKeyboardButton(text="📅 +2 недели", callback_data="days:14")
    )
    builder.row(
        InlineKeyboardButton(text="📅 +3 недели", callback_data="days:21"),
        InlineKeyboardButton(text="📅 +1 месяц", callback_data="days:30")
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Другой срок", callback_data="days:custom")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="catalog")
    )
    
    return builder.as_markup()


def wizard_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение в визарде (ТЗ 3.1.6)"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Всё верно, добавить", callback_data="wizard_confirm")
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Исправить", callback_data="wizard_edit"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="wizard_cancel")
    )
    
    return builder.as_markup()


def wizard_skip_photo_keyboard() -> InlineKeyboardMarkup:
    """Пропустить фото в визарде"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_photo")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отменить", callback_data="wizard_cancel")
    )
    
    return builder.as_markup()


def users_selection_keyboard(users: List[Dict]) -> InlineKeyboardMarkup:
    """Выбор владельца книги (Шаг 5 визарда)"""
    builder = InlineKeyboardBuilder()
    
    for user in users[:10]:  # Ограничиваем до 10 пользователей
        name = user.get("username") or user.get("full_name", "Неизвестный")
        builder.row(
            InlineKeyboardButton(
                text=f"👤 {name}",
                callback_data=f"select_owner:{user['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="❌ Отменить", callback_data="wizard_cancel")
    )
    
    return builder.as_markup()


def edit_field_keyboard() -> InlineKeyboardMarkup:
    """Выбор поля для редактирования (ТЗ 4.1)"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✏️ Автор", callback_data="edit_field:author"),
        InlineKeyboardButton(text="✏️ Название", callback_data="edit_field:title")
    )
    builder.row(
        InlineKeyboardButton(text="🖼 Фото", callback_data="edit_field:photo"),
        InlineKeyboardButton(text="📝 Описание", callback_data="edit_field:description")
    )
    builder.row(
        InlineKeyboardButton(text="📚 Жанр", callback_data="edit_field:genre")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="catalog")
    )
    
    return builder.as_markup()


def return_photo_keyboard() -> InlineKeyboardMarkup:
    """Загрузка фото при возврате (ТЗ 3.3.2)"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⏭ Пропустить фото", callback_data="skip_return_photo")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="my_books")
    )
    
    return builder.as_markup()


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Админ панель"""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="📋 Заявки на бронь", callback_data="admin_reservations")
    )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить книгу", callback_data="add_book")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")
    )

    return builder.as_markup()


def pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str
) -> InlineKeyboardMarkup:
    """Пагинация для списков"""
    builder = InlineKeyboardBuilder()
    
    buttons = []
    if current_page > 0:
        buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{callback_prefix}:{current_page - 1}")
        )
    
    buttons.append(
        InlineKeyboardButton(text=f"📄 {current_page + 1}/{total_pages}", callback_data="noop")
    )
    
    if current_page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton(text="➡️ Вперед", callback_data=f"{callback_prefix}:{current_page + 1}")
        )
    
    builder.row(*buttons)
    
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Простая кнопка отмены"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    )
    return builder.as_markup()
