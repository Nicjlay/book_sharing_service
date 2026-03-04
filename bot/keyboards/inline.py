"""
Inline клавиатуры для бота
"""
from typing import Dict, List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Telegram ограничивает inline-клавиатуры 100 кнопками.
# Лимит жанров предотвращает BadRequest при большом списке от API.
MAX_GENRES = 30


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📖 Каталог", callback_data="catalog"),
        InlineKeyboardButton(text="🔍 Поиск", callback_data="search"),
    )
    builder.row(
        InlineKeyboardButton(text="📚 Мои книги", callback_data="my_books"),
        InlineKeyboardButton(text="📖 Книги на руках", callback_data="my_borrowed"),
    )
    if is_admin:
        builder.row(InlineKeyboardButton(text="👨‍💼 Админ панель", callback_data="admin_panel"))
    return builder.as_markup()


def catalog_filters_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📚 Все книги", callback_data="filter:all"),
        InlineKeyboardButton(text="🟢 Доступные", callback_data="filter:available"),
    )
    builder.row(InlineKeyboardButton(text="📖 По жанрам", callback_data="filter:genres"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"))
    return builder.as_markup()


def genres_keyboard(genres: List[str]) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора жанра.

    ВАЖНО: callback_data Telegram ограничен 64 байтами.
    Кириллические символы занимают 2 байта в UTF-8, поэтому строка
    «genre:Современная проза» (23 кириллических символа = 46 байт + 6 префикс = 52 байта)
    уже близка к лимиту, а более длинные названия его превысят → BadRequest.

    Решение: используем индекс жанра в callback_data («genre_idx:N»),
    а сам список genres хранится в FSM state (сохраняется в apply_filter в catalog.py).
    Индекс гарантированно помещается в 64 байта (genre_idx:999 = 12 байт).
    """
    builder = InlineKeyboardBuilder()
    display = genres[:MAX_GENRES]
    for i, genre in enumerate(display):
        builder.row(InlineKeyboardButton(text=genre, callback_data=f"genre_idx:{i}"))
    if len(genres) > MAX_GENRES:
        builder.row(InlineKeyboardButton(
            text=f"(ещё {len(genres) - MAX_GENRES} жанров — используйте поиск)",
            callback_data="noop",
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="catalog"))
    return builder.as_markup()


def book_card_keyboard(
    book: Dict,
    user_id: int,
    is_admin: bool = False,
    in_waitlist: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура карточки книги.

    Параметр in_waitlist: если True — показывает кнопку «Покинуть очередь»
    вместо «Уведомить меня». Это решает UX-баг: без этого параметра
    пользователь, уже стоящий в очереди, не имел способа из неё выйти.
    """
    builder = InlineKeyboardBuilder()
    status = book.get("status")
    owner_id = book.get("owner_id")
    borrower_id = book.get("borrower_id")
    book_id = book.get("id", 0)

    if status == "available":
        if owner_id != user_id:
            builder.row(InlineKeyboardButton(text="🟢 Забронировать", callback_data=f"reserve:{book_id}"))
    elif status == "reserved":
        if borrower_id == user_id:
            builder.row(InlineKeyboardButton(text="⏳ Ожидает подтверждения", callback_data="noop"))
        elif is_admin:
            builder.row(
                InlineKeyboardButton(text="✅ Подтвердить выдачу", callback_data=f"approve:{book_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{book_id}"),
            )
    elif status in ("borrowed", "overdue"):
        if borrower_id == user_id or owner_id == user_id or is_admin:
            builder.row(InlineKeyboardButton(text="↩️ Вернуть книгу", callback_data=f"return:{book_id}"))
        else:
            # Показываем кнопку «Покинуть очередь» если пользователь уже в ней,
            # иначе — кнопку вступления.
            if in_waitlist:
                builder.row(InlineKeyboardButton(
                    text="🔕 Покинуть очередь",
                    callback_data=f"leave_waitlist:{book_id}",
                ))
            else:
                builder.row(InlineKeyboardButton(
                    text="🔔 Уведомить меня",
                    callback_data=f"waitlist:{book_id}",
                ))

    # Кнопки редактирования/удаления: доступны для available и overdue.
    # Для borrowed/reserved скрываем — API отклонит эти операции.
    if owner_id == user_id and status not in ("borrowed", "reserved"):
        builder.row(
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{book_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{book_id}"),
        )
    elif is_admin and status not in ("borrowed", "reserved"):
        builder.row(
            InlineKeyboardButton(text="✏️ Редактировать (admin)", callback_data=f"edit:{book_id}"),
            InlineKeyboardButton(text="🗑 Удалить (admin)", callback_data=f"delete:{book_id}"),
        )

    builder.row(InlineKeyboardButton(text="📜 История", callback_data=f"history:{book_id}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад к каталогу", callback_data="catalog"))
    return builder.as_markup()


def borrowed_books_keyboard(books: List[Dict]) -> InlineKeyboardMarkup:
    """Список книг на руках"""
    builder = InlineKeyboardBuilder()
    status_emoji = {"borrowed": "🔴", "overdue": "⏳", "reserved": "🟡"}
    for book in books:
        emoji = status_emoji.get(book.get("status"), "📖")
        # FIX: book.get("title", "—") вместо book["title"] — KeyError если title отсутствует.
        # API обязан возвращать title, но защита от некорректного ответа не помешает.
        raw_title = book.get("title", "—")
        title = raw_title[:28] + ".." if len(raw_title) > 28 else raw_title
        book_id = book.get("id", 0)
        builder.row(InlineKeyboardButton(
            text=f"{emoji} {title}",
            callback_data=f"borrowed_detail:{book_id}",
        ))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))
    return builder.as_markup()


def borrowed_book_actions_keyboard(book_id: int) -> InlineKeyboardMarkup:
    """Действия с книгой на руках"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="↩️ Вернуть книгу", callback_data=f"return:{book_id}"))
    builder.row(InlineKeyboardButton(text="📜 История", callback_data=f"history:{book_id}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад к списку", callback_data="my_borrowed"))
    return builder.as_markup()


def reservation_days_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 +1 неделя", callback_data="days:7"),
        InlineKeyboardButton(text="📅 +2 недели", callback_data="days:14"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 +3 недели", callback_data="days:21"),
        InlineKeyboardButton(text="📅 +1 месяц", callback_data="days:30"),
    )
    builder.row(InlineKeyboardButton(text="✏️ Другой срок", callback_data="days:custom"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="catalog"))
    return builder.as_markup()


def wizard_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Всё верно, добавить", callback_data="wizard_confirm"))
    builder.row(
        InlineKeyboardButton(text="✏️ Исправить", callback_data="wizard_edit"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="wizard_cancel"),
    )
    return builder.as_markup()


def wizard_skip_photo_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_photo"))
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="wizard_cancel"))
    return builder.as_markup()


def users_selection_keyboard(users: List[Dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users[:10]:
        # FIX: user.get("id", 0) вместо user["id"] — KeyError если поле отсутствует.
        # Пользователей без id пропускаем: 0 не является валидным Telegram user_id,
        # и выбор такого пользователя владельцем книги создал бы некорректную запись в БД.
        user_id = user.get("id")
        if not user_id:
            continue
        name = user.get("username") or user.get("full_name", "Неизвестный")
        builder.row(InlineKeyboardButton(
            text=f"👤 {name}",
            callback_data=f"select_owner:{user_id}",
        ))
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="wizard_cancel"))
    return builder.as_markup()


def edit_field_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✏️ Автор", callback_data="edit_field:author"),
        InlineKeyboardButton(text="✏️ Название", callback_data="edit_field:title"),
    )
    builder.row(
        InlineKeyboardButton(text="🖼 Фото", callback_data="edit_field:photo"),
        InlineKeyboardButton(text="📝 Описание", callback_data="edit_field:description"),
    )
    builder.row(InlineKeyboardButton(text="📚 Жанр", callback_data="edit_field:genre"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="catalog"))
    return builder.as_markup()


def return_photo_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏭ Пропустить фото", callback_data="skip_return_photo"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="my_books"))
    return builder.as_markup()


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 Заявки на бронь", callback_data="admin_reservations"))
    builder.row(InlineKeyboardButton(text="➕ Добавить книгу", callback_data="add_book"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))
    return builder.as_markup()


def pagination_keyboard(current_page: int, total_pages: int, callback_prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = []
    if current_page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{callback_prefix}:{current_page - 1}"))
    buttons.append(InlineKeyboardButton(text=f"📄 {current_page + 1}/{total_pages}", callback_data="noop"))
    if current_page < total_pages - 1:
        buttons.append(InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"{callback_prefix}:{current_page + 1}"))
    builder.row(*buttons)
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()