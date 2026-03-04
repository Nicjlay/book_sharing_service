"""
Форматирование сообщений для отображения книг и истории
"""
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

# Максимальное число записей истории для отображения.
# Вынесено на уровень модуля: используется и в format_history (отображение),
# и потенциально в тестах. Магическая константа внутри функции затрудняет
# обнаружение лимита при ревью.
_HISTORY_DISPLAY_LIMIT = 30


def escape_html(text: str) -> str:
    """
    Экранирование HTML-спецсимволов.
    Используется везде, где пользовательский ввод вставляется в HTML-разметку
    Telegram-сообщений. Без этого атакующий может внедрить теги и изменить UI.
    """
    if not text:
        return ""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _user_link(user_id, username=None, full_name=None) -> str:
    """Формирует кликабельную ссылку на пользователя Telegram"""
    if username:
        label = "@" + escape_html(username)
    elif full_name:
        label = escape_html(full_name)
    else:
        label = "Пользователь"
    if user_id:
        return f'<a href="tg://user?id={user_id}">{label}</a>'
    return label


def format_book_card(book: Dict) -> str:
    """Форматирование карточки книги с emoji статусов"""
    status_emoji = {
        "available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"
    }
    status = book.get("status", "available")
    emoji = status_emoji.get(status, "⚪️")
    status_text = {
        "available": "Свободна",
        "reserved": "Забронирована (ожидает выдачи)",
        "borrowed": "Выдана",
        "overdue": "Просрочена",
    }

    safe_title = escape_html(book.get("title", "—"))
    safe_author = escape_html(book.get("author", "—"))

    text = f"📖 <b>{safe_title}</b>\n"
    text += f"✍️ {safe_author}\n"
    text += f"{emoji} <b>Статус:</b> {status_text.get(status, escape_html(status))}\n"

    owner_link = _user_link(book.get("owner_id"), book.get("owner_username"), book.get("owner_full_name"))
    text += f"👤 <b>Владелец:</b> {owner_link}\n"

    if status in ("borrowed", "overdue", "reserved"):
        borrower_link = _user_link(
            book.get("borrower_id"), book.get("borrower_username"), book.get("borrower_full_name")
        )
        text += f"📱 <b>У читателя:</b> {borrower_link}\n"
        if book.get("return_due_date"):
            try:
                due_date = datetime.fromisoformat(book["return_due_date"].replace("Z", "+00:00"))
                text += f"📅 <b>Вернуть до:</b> {due_date.strftime('%d.%m.%Y')}\n"
            except (ValueError, TypeError) as e:
                # Логируем некорректную дату вместо молчаливого игнорирования.
                logger.warning("Invalid return_due_date %r for book %s: %s",
                               book.get("return_due_date"), book.get("id"), e)

    if book.get("genre"):
        text += f"📚 <b>Жанр:</b> {escape_html(book['genre'])}\n"
    if book.get("description"):
        desc = book["description"]
        if len(desc) > 200:
            desc = desc[:200] + "..."
        text += f"\n📝 {escape_html(desc)}\n"

    # FIX: book.get('id', 0) вместо book['id'] — защита от KeyError,
    # если API вернул объект без поля id (edge case при ошибке сериализации).
    book_id = book.get("id", 0)
    text += f"\n🔖 <b>ID:</b> #{book_id:05d}"
    return text


def format_book_list(books: List[Dict], page: int = 0, per_page: int = 5) -> str:
    """Форматирование списка книг с пагинацией"""
    if not books:
        return "📚 Книги не найдены"
    start = page * per_page
    end = start + per_page
    page_books = books[start:end]
    total_pages = max(1, (len(books) - 1) // per_page + 1)
    text = f"📚 <b>Найдено книг:</b> {len(books)}\n"
    text += f"📄 <b>Страница:</b> {page + 1}/{total_pages}\n\n"
    status_emoji = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}
    for book in page_books:
        emoji = status_emoji.get(book.get("status", "available"), "⚪️")
        # FIX: book.get('id', 0) вместо book['id']
        book_id = book.get("id", 0)
        text += f"{emoji} <b>{escape_html(book.get('title', '—'))}</b>\n"
        text += f"   ✍️ {escape_html(book.get('author', '—'))}\n"
        text += f"   🔖 ID: #{book_id:05d}\n\n"
    return text


def format_history(history: List[Dict]) -> str:
    """Форматирование истории книги с кликабельными именами читателей"""
    if not history:
        return "📜 История пуста"

    truncated = len(history) > _HISTORY_DISPLAY_LIMIT
    history = history[-_HISTORY_DISPLAY_LIMIT:]

    text = "📜 <b>История книги:</b>\n\n"
    if truncated:
        text += f"<i>(показаны последние {_HISTORY_DISPLAY_LIMIT} записей)</i>\n\n"

    for entry in history:
        raw_date = entry.get("created_at") or entry.get("date") or entry.get("timestamp")
        if raw_date:
            try:
                date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                date_str = date.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError) as e:
                logger.warning("Invalid date in history entry: %r — %s", raw_date, e)
                date_str = escape_html(str(raw_date))
        else:
            date_str = "-"

        comment = entry.get("comment", "")
        status = entry.get("status_to") or entry.get("status", "")
        user_id = entry.get("user_id")
        username = entry.get("username") or entry.get("user_username")
        full_name = entry.get("full_name") or entry.get("user_full_name")
        user_link = _user_link(user_id, username, full_name)

        text += f"📅 {date_str}\n"
        text += f"   👤 {user_link}\n"
        if comment:
            text += f"   {escape_html(comment)}\n"
        elif status:
            text += f"   {escape_html(status)}\n"
        if entry.get("photo_proof_path"):
            text += "   📸 Фото приложено\n"
        text += "\n"
    return text


def format_wizard_preview(data: Dict) -> str:
    """Предпросмотр книги в визарде перед добавлением"""
    text = "📖 <b>Предпросмотр книги</b>\n\n"
    text += f"<b>Название:</b> {escape_html(data.get('title', 'Не указано'))}\n"
    text += f"<b>Автор:</b> {escape_html(data.get('author', 'Не указано'))}\n"
    if data.get("genre"):
        text += f"<b>Жанр:</b> {escape_html(data['genre'])}\n"
    if data.get("description"):
        text += f"<b>Описание:</b> {escape_html(data['description'])}\n"
    text += f"<b>Владелец:</b> {escape_html(data.get('owner_name', 'Не выбран'))}\n"
    if data.get("has_photo"):
        text += "\n✅ Фото обложки загружено"
    else:
        text += "\n⚠️ Фото обложки не загружено (будет использована заглушка)"
    text += "\n\n<b>Всё верно?</b>"
    return text


def format_my_books(books: List[Dict], user_id: int) -> str:
    """Форматирование списка 'Мои книги'"""
    if not books:
        return "📚 У вас пока нет книг"
    owned = [b for b in books if b.get("owner_id") == user_id]
    borrowed = [b for b in books if b.get("borrower_id") == user_id]
    text = "📚 <b>Мои книги:</b>\n\n"
    if owned:
        text += "🏠 <b>Мои книги (владелец):</b>\n"
        status_emoji = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}
        for book in owned:
            emoji = status_emoji.get(book.get("status"), "⚪️")
            # FIX: book.get('id', 0) вместо book['id']
            book_id = book.get("id", 0)
            text += f"{emoji} {escape_html(book.get('title', '—'))} (ID: #{book_id:05d})\n"
        text += "\n"
    if borrowed:
        text += "📖 <b>Книги на руках:</b>\n"
        for book in borrowed:
            due_date = ""
            if book.get("return_due_date"):
                try:
                    date = datetime.fromisoformat(book["return_due_date"].replace("Z", "+00:00"))
                    due_date = f" - до {date.strftime('%d.%m.%Y')}"
                except (ValueError, TypeError) as e:
                    logger.warning("Invalid return_due_date in my_books: %r — %s",
                                   book.get("return_due_date"), e)
            status_emoji_borrowed = {"borrowed": "🔴", "overdue": "⏳"}
            emoji = status_emoji_borrowed.get(book.get("status"), "📖")
            # FIX: book.get('id', 0) вместо book['id']
            book_id = book.get("id", 0)
            text += f"{emoji} {escape_html(book.get('title', '—'))}{due_date} (ID: #{book_id:05d})\n"
    return text


def format_notification(notification: Dict) -> str:
    """Форматирование уведомления от API"""
    msg_type = notification.get("type")
    message = notification.get("message", "")
    emoji_map = {
        "reservation_approved": "✅",
        "reservation_rejected": "❌",
        "book_returned": "📚",
        "waitlist_available": "🔥",
        "overdue": "🚨",
        "new_book": "📖",
        "due_date_reminder": "⏰",
        "admin_reservation_request": "📩",
    }
    emoji = emoji_map.get(msg_type, "📢")
    return f"{emoji} <b>Уведомление</b>\n\n{message}"
