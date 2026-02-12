"""
Форматирование сообщений для отображения книг и истории
"""
from typing import Dict, List
from datetime import datetime


def format_book_card(book: Dict) -> str:
    """
    Форматирование карточки книги с emoji статусами (ТЗ 4.2)
    """
    # Emoji для статусов
    status_emoji = {
        "available": "🟢",
        "reserved": "🟡",
        "borrowed": "🔴",
        "overdue": "⏳"
    }
    
    status = book.get("status", "available")
    emoji = status_emoji.get(status, "⚪️")
    
    # Статус текстом
    status_text = {
        "available": "Свободна",
        "reserved": "Забронирована (ожидает выдачи)",
        "borrowed": "Выдана",
        "overdue": "Просрочена"
    }
    
    text = f"📖 <b>{book['title']}</b>\n"
    text += f"✍️ {book['author']}\n"
    text += f"{emoji} <b>Статус:</b> {status_text.get(status, status)}\n"
    
    # Владелец
    owner_name = book.get("owner_username") or book.get("owner_full_name", "Неизвестен")
    text += f"👤 <b>Владелец:</b> {owner_name}\n"
    
    # Если книга выдана - показываем заемщика
    if status in ["borrowed", "overdue", "reserved"]:
        borrower_name = book.get("borrower_username") or book.get("borrower_full_name", "Неизвестен")
        text += f"📱 <b>У читателя:</b> {borrower_name}\n"
        
        if book.get("return_due_date"):
            due_date = datetime.fromisoformat(book["return_due_date"].replace("Z", "+00:00"))
            text += f"📅 <b>Вернуть до:</b> {due_date.strftime('%d.%m.%Y')}\n"
    
    # Жанр
    if book.get("genre"):
        text += f"📚 <b>Жанр:</b> {book['genre']}\n"
    
    # Описание
    if book.get("description"):
        desc = book["description"]
        if len(desc) > 200:
            desc = desc[:200] + "..."
        text += f"\n📝 {desc}\n"
    
    # ID книги
    text += f"\n🔖 <b>ID:</b> #{book['id']:05d}"
    
    return text


def format_book_list(books: List[Dict], page: int = 0, per_page: int = 5) -> str:
    """
    Форматирование списка книг с пагинацией
    """
    if not books:
        return "📚 Книги не найдены"
    
    start = page * per_page
    end = start + per_page
    page_books = books[start:end]
    
    text = f"📚 <b>Найдено книг:</b> {len(books)}\n"
    text += f"📄 <b>Страница:</b> {page + 1} / {(len(books) - 1) // per_page + 1}\n\n"
    
    status_emoji = {
        "available": "🟢",
        "reserved": "🟡",
        "borrowed": "🔴",
        "overdue": "⏳"
    }
    
    for book in page_books:
        emoji = status_emoji.get(book.get("status", "available"), "⚪️")
        text += f"{emoji} <b>{book['title']}</b>\n"
        text += f"   ✍️ {book['author']}\n"
        text += f"   🔖 ID: #{book['id']:05d}\n\n"
    
    return text


def format_history(history: List[Dict]) -> str:
    """
    Форматирование истории книги (ТЗ 4.4)
    """
    if not history:
        return "📜 История пуста"
    
    text = "📜 <b>История книги:</b>\n\n"
    
    for entry in history:
        date = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))
        date_str = date.strftime("%d.%m.%Y %H:%M")
        
        comment = entry.get("comment", "")
        
        text += f"📅 {date_str}\n"
        text += f"   {comment}\n"
        
        if entry.get("photo_proof_path"):
            text += f"   📸 Фото приложено\n"
        
        text += "\n"
    
    return text


def format_wizard_preview(data: Dict) -> str:
    """
    Предпросмотр книги в визарде перед добавлением (ТЗ 3.1.6)
    """
    text = "📖 <b>Предпросмотр книги</b>\n\n"
    text += f"<b>Название:</b> {data.get('title', 'Не указано')}\n"
    text += f"<b>Автор:</b> {data.get('author', 'Не указано')}\n"
    
    if data.get("genre"):
        text += f"<b>Жанр:</b> {data['genre']}\n"
    
    if data.get("description"):
        text += f"<b>Описание:</b> {data['description']}\n"
    
    owner_name = data.get("owner_name", "Не выбран")
    text += f"<b>Владелец:</b> {owner_name}\n"
    
    if data.get("image_path"):
        text += f"\n✅ Фото обложки загружено"
    else:
        text += f"\n⚠️ Фото обложки не загружено (будет использована заглушка)"
    
    text += "\n\n<b>Всё верно?</b>"
    
    return text


def format_my_books(books: List[Dict], user_id: int) -> str:
    """
    Форматирование списка 'Мои книги'
    """
    if not books:
        return "📚 У вас пока нет книг"
    
    owned = [b for b in books if b.get("owner_id") == user_id]
    borrowed = [b for b in books if b.get("borrower_id") == user_id]
    
    text = "📚 <b>Мои книги:</b>\n\n"
    
    if owned:
        text += "🏠 <b>Мои книги (владелец):</b>\n"
        for book in owned:
            status_emoji = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}
            emoji = status_emoji.get(book.get("status"), "⚪️")
            text += f"{emoji} {book['title']} (ID: #{book['id']:05d})\n"
        text += "\n"
    
    if borrowed:
        text += "📖 <b>Книги на руках:</b>\n"
        for book in borrowed:
            due_date = ""
            if book.get("return_due_date"):
                date = datetime.fromisoformat(book["return_due_date"].replace("Z", "+00:00"))
                due_date = f" - до {date.strftime('%d.%m.%Y')}"
            
            status_emoji = {"borrowed": "🔴", "overdue": "⏳"}
            emoji = status_emoji.get(book.get("status"), "📖")
            text += f"{emoji} {book['title']}{due_date} (ID: #{book['id']:05d})\n"
    
    return text


def format_notification(notification: Dict) -> str:
    """
    Форматирование уведомления от API
    """
    msg_type = notification.get("type")
    message = notification.get("message", "")
    
    # Добавляем emoji в зависимости от типа
    emoji_map = {
        "reservation_approved": "✅",
        "reservation_rejected": "❌",
        "book_returned": "📚",
        "waitlist_available": "🔥",
        "overdue": "🚨",
        "new_book": "📖",
        "due_date_reminder": "⏰"
    }
    
    emoji = emoji_map.get(msg_type, "📢")
    
    return f"{emoji} <b>Уведомление</b>\n\n{message}"


def escape_html(text: str) -> str:
    """Экранирование HTML символов"""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
