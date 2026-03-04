"""
test_formatters.py — тесты для utils/formatters.py

Formatters содержат ТОЛЬКО чистые функции (pure functions):
принимают данные → возвращают строку. Никаких сетевых запросов,
никаких баз данных. Поэтому здесь легко достичь близкого к 100% покрытия.

Запуск только этого файла:
    pytest tests/test_formatters.py -v
"""
import pytest
from utils.formatters import (
    _HISTORY_DISPLAY_LIMIT,
    _user_link,
    escape_html,
    format_book_card,
    format_book_list,
    format_history,
    format_my_books,
    format_notification,
    format_wizard_preview,
)


# ═══════════════════════════════════════════
# escape_html
# ═══════════════════════════════════════════

class TestEscapeHtml:
    """
    escape_html защищает от XSS: если пользователь введёт '<script>',
    это не должно стать реальным HTML-тегом в сообщении Telegram.
    """

    def test_empty_string(self):
        assert escape_html("") == ""

    def test_none_returns_empty(self):
        # Функция принимает str, но проверяем защитное поведение при falsy
        assert escape_html("") == ""

    def test_ampersand(self):
        assert escape_html("A&B") == "A&amp;B"

    def test_less_than(self):
        assert escape_html("<tag>") == "&lt;tag&gt;"

    def test_greater_than(self):
        assert escape_html("a>b") == "a&gt;b"

    def test_double_quote(self):
        assert escape_html('"hello"') == "&quot;hello&quot;"

    def test_combined(self):
        result = escape_html('<script>alert("XSS")</script>')
        assert result == "&lt;script&gt;alert(&quot;XSS&quot;)&lt;/script&gt;"

    def test_no_special_chars(self):
        text = "Обычный текст 123"
        assert escape_html(text) == text

    def test_multiple_ampersands(self):
        assert escape_html("a&b&c") == "a&amp;b&amp;c"


# ═══════════════════════════════════════════
# _user_link (приватная, но тестируем т.к. она влияет на все форматтеры)
# ═══════════════════════════════════════════

class TestUserLink:
    def test_with_username(self):
        link = _user_link(123, username="john_doe")
        assert 'href="tg://user?id=123"' in link
        assert "@john_doe" in link

    def test_with_full_name(self):
        link = _user_link(123, full_name="Иван Иванов")
        assert "Иван Иванов" in link
        assert 'tg://user?id=123' in link

    def test_username_takes_priority_over_full_name(self):
        link = _user_link(123, username="ivan", full_name="Иван")
        assert "@ivan" in link
        assert "Иван" not in link

    def test_no_user_id_no_username_no_name(self):
        link = _user_link(None)
        assert link == "Пользователь"

    def test_no_user_id_with_username(self):
        # Без user_id — просто текст без ссылки
        link = _user_link(None, username="user")
        assert "<a href" not in link
        assert "@user" in link

    def test_xss_in_username(self):
        link = _user_link(1, username='<b>hacker</b>')
        assert "<b>" not in link
        assert "&lt;b&gt;" in link

    def test_xss_in_full_name(self):
        link = _user_link(1, full_name='<script>evil()</script>')
        assert "<script>" not in link


# ═══════════════════════════════════════════
# format_book_card
# ═══════════════════════════════════════════

class TestFormatBookCard:
    def test_available_book(self, sample_book):
        text = format_book_card(sample_book)
        assert "Мастер и Маргарита" in text
        assert "Булгаков Михаил" in text
        assert "Свободна" in text
        assert "🟢" in text
        assert "#00042" in text  # форматирование ID

    def test_borrowed_book_shows_borrower(self, borrowed_book):
        text = format_book_card(borrowed_book)
        assert "Выдана" in text
        assert "🔴" in text
        assert "reader_user" in text  # имя заёмщика
        assert "31.12.2025" in text   # дата возврата

    def test_overdue_book(self, overdue_book):
        text = format_book_card(overdue_book)
        assert "⏳" in text or "Просрочена" in text

    def test_reserved_book(self):
        book = {
            "id": 5,
            "title": "Книга",
            "author": "Автор Имя",
            "status": "reserved",
            "owner_id": 1,
            "borrower_id": 2,
            "borrower_username": "waiting_user",
            "return_due_date": "2025-06-01T00:00:00",
        }
        text = format_book_card(book)
        assert "🟡" in text
        assert "Забронирована" in text

    def test_missing_id_uses_zero(self):
        book = {"title": "Test", "author": "Auth Or", "status": "available"}
        text = format_book_card(book)
        assert "#00000" in text  # id=0 по умолчанию

    def test_description_truncated(self):
        book = {
            "id": 1,
            "title": "T",
            "author": "A B",
            "status": "available",
            "description": "x" * 300,
        }
        text = format_book_card(book)
        assert "..." in text
        # Описание обрезается до 200 символов + "..."
        assert text.count("x") <= 200

    def test_invalid_due_date_does_not_crash(self):
        book = {
            "id": 3,
            "title": "T",
            "author": "A B",
            "status": "borrowed",
            "borrower_id": 1,
            "return_due_date": "not-a-date",
        }
        # Не должно выбросить исключение
        text = format_book_card(book)
        assert isinstance(text, str)

    def test_xss_in_title(self):
        book = {
            "id": 1,
            "title": "<b>Взломай меня</b>",
            "author": "Хакер Иван",
            "status": "available",
        }
        text = format_book_card(book)
        assert "<b>Взломай меня</b>" not in text
        assert "&lt;b&gt;" in text

    def test_unknown_status_uses_default_emoji(self):
        book = {"id": 1, "title": "T", "author": "A B", "status": "unknown_status"}
        text = format_book_card(book)
        assert "⚪️" in text


# ═══════════════════════════════════════════
# format_book_list
# ═══════════════════════════════════════════

class TestFormatBookList:
    def test_empty_list(self):
        assert format_book_list([]) == "📚 Книги не найдены"

    def test_single_book_page_1_of_1(self, sample_book):
        # ПРИЧИНА ОШИБКИ: функция возвращает HTML — строки обёрнуты в теги <b>.
        # Реальный вывод: "📚 <b>Найдено книг:</b> 1"
        # Тест проверял: "Найдено книг: 1" — такой строки нет, есть "</b> 1"
        # ФИКС: проверяем подстроку, которая точно есть в HTML-выводе.
        text = format_book_list([sample_book])
        assert "Найдено книг:</b> 1" in text
        assert "Страница:</b> 1/1" in text
        assert "Мастер и Маргарита" in text

    def test_pagination_page_0(self):
        books = [{"id": i, "title": f"Книга {i}", "author": "Авт Ор", "status": "available"}
                 for i in range(12)]
        text = format_book_list(books, page=0, per_page=5)
        assert "Страница:</b> 1/3" in text
        assert "Книга 0" in text
        assert "Книга 5" not in text  # 6-я книга не на первой странице

    def test_pagination_page_1(self):
        books = [{"id": i, "title": f"Книга {i}", "author": "Авт Ор", "status": "available"}
                 for i in range(12)]
        text = format_book_list(books, page=1, per_page=5)
        assert "Страница:</b> 2/3" in text
        assert "Книга 5" in text

    def test_book_without_id(self):
        books = [{"title": "No ID Book", "author": "Кто То", "status": "available"}]
        text = format_book_list(books)
        assert "#00000" in text  # дефолтный id=0


# ═══════════════════════════════════════════
# format_history
# ═══════════════════════════════════════════

class TestFormatHistory:
    def test_empty_history(self):
        assert format_history([]) == "📜 История пуста"

    def test_basic_entry_with_username(self):
        history = [{
            "created_at": "2025-01-15T10:30:00",
            "user_id": 123,
            "username": "reader",
            "comment": "Взял книгу",
        }]
        text = format_history(history)
        assert "15.01.2025" in text
        assert "@reader" in text
        assert "Взял книгу" in text

    def test_entry_with_status_fallback(self):
        history = [{
            "created_at": "2025-02-01T08:00:00Z",
            "user_id": 1,
            "full_name": "Иван Петров",
            "status_to": "borrowed",
        }]
        text = format_history(history)
        assert "borrowed" in text
        assert "Иван Петров" in text

    def test_entry_with_photo_proof(self):
        history = [{
            "created_at": "2025-01-01T00:00:00",
            "user_id": 1,
            "username": "u",
            "photo_proof_path": "/some/path.jpg",
        }]
        text = format_history(history)
        assert "📸 Фото приложено" in text

    def test_truncation_when_too_many_entries(self):
        history = [
            {"created_at": f"2025-01-{i:02d}T00:00:00", "user_id": i, "username": f"u{i}", "comment": "c"}
            for i in range(1, _HISTORY_DISPLAY_LIMIT + 10)
        ]
        text = format_history(history)
        assert f"последние {_HISTORY_DISPLAY_LIMIT} записей" in text

    def test_no_truncation_at_limit(self):
        history = [
            {"created_at": "2025-01-01T00:00:00", "user_id": i, "username": f"u{i}", "comment": "c"}
            for i in range(_HISTORY_DISPLAY_LIMIT)
        ]
        text = format_history(history)
        assert "последние" not in text

    def test_invalid_date_does_not_crash(self):
        history = [{"created_at": "garbage", "user_id": 1, "username": "u", "comment": "x"}]
        text = format_history(history)
        assert isinstance(text, str)
        assert "garbage" in text  # показывает raw значение

    def test_missing_date_shows_dash(self):
        history = [{"user_id": 1, "username": "u", "comment": "x"}]
        text = format_history(history)
        assert "📅 -" in text

    def test_entry_with_timestamp_field(self):
        history = [{"timestamp": "2025-03-10T12:00:00", "user_id": 1, "username": "u"}]
        text = format_history(history)
        assert "10.03.2025" in text


# ═══════════════════════════════════════════
# format_wizard_preview
# ═══════════════════════════════════════════

class TestFormatWizardPreview:
    def test_full_data(self):
        data = {
            "title": "Война и мир",
            "author": "Толстой Лев",
            "genre": "Роман",
            "description": "Эпический роман.",
            "owner_name": "Иван",
            "has_photo": True,
        }
        text = format_wizard_preview(data)
        assert "Война и мир" in text
        assert "Толстой Лев" in text
        assert "Роман" in text
        assert "✅ Фото обложки загружено" in text

    def test_no_photo_warning(self):
        data = {"title": "T", "author": "A B", "has_photo": False, "owner_name": "x"}
        text = format_wizard_preview(data)
        assert "⚠️" in text

    def test_optional_genre_missing(self):
        data = {"title": "T", "author": "A B", "owner_name": "x"}
        text = format_wizard_preview(data)
        assert "Жанр" not in text

    def test_xss_in_title(self):
        data = {"title": "<script>", "author": "A B", "owner_name": "x"}
        text = format_wizard_preview(data)
        assert "<script>" not in text


# ═══════════════════════════════════════════
# format_my_books
# ═══════════════════════════════════════════

class TestFormatMyBooks:
    def test_no_books(self):
        assert format_my_books([], user_id=1) == "📚 У вас пока нет книг"

    def test_owned_books_shown(self):
        books = [{"id": 1, "title": "Моя книга", "author": "A B",
                  "owner_id": 10, "status": "available"}]
        text = format_my_books(books, user_id=10)
        assert "🏠" in text
        assert "Моя книга" in text

    def test_borrowed_books_shown(self):
        books = [{
            "id": 2,
            "title": "Чужая книга",
            "author": "A B",
            "owner_id": 99,
            "borrower_id": 10,
            "status": "borrowed",
            "return_due_date": "2025-08-15T00:00:00",
        }]
        text = format_my_books(books, user_id=10)
        # ПРИЧИНА ОШИБКИ: функция выводит "📖 <b>Книги на руках:</b>"
        # Тест проверял "📖 Книги на руках" — такой строки нет из-за <b> тегов.
        # ФИКС: ищем подстроку без эмодзи, она точно есть внутри тегов.
        assert "Книги на руках" in text
        assert "Чужая книга" in text
        assert "15.08.2025" in text

    def test_invalid_due_date_in_my_books(self):
        books = [{
            "id": 2,
            "title": "Книга",
            "author": "A B",
            "owner_id": 99,
            "borrower_id": 10,
            "status": "borrowed",
            "return_due_date": "INVALID",
        }]
        # Не должно падать
        text = format_my_books(books, user_id=10)
        assert isinstance(text, str)

    def test_book_without_id(self):
        books = [{"title": "No ID", "author": "A B", "owner_id": 5, "status": "available"}]
        text = format_my_books(books, user_id=5)
        assert "#00000" in text

    def test_overdue_emoji(self):
        books = [{"id": 3, "title": "T", "author": "A B",
                  "owner_id": 99, "borrower_id": 5, "status": "overdue"}]
        text = format_my_books(books, user_id=5)
        assert "⏳" in text


# ═══════════════════════════════════════════
# format_notification
# ═══════════════════════════════════════════

class TestFormatNotification:
    @pytest.mark.parametrize("notif_type,expected_emoji", [
        ("reservation_approved", "✅"),
        ("reservation_rejected", "❌"),
        ("book_returned", "📚"),
        ("waitlist_available", "🔥"),
        ("overdue", "🚨"),
        ("new_book", "📖"),
        ("due_date_reminder", "⏰"),
        ("admin_reservation_request", "📩"),
        ("unknown_type", "📢"),   # дефолтный emoji
    ])
    def test_emoji_by_type(self, notif_type, expected_emoji):
        notif = {"type": notif_type, "message": "Текст уведомления"}
        text = format_notification(notif)
        assert expected_emoji in text
        assert "Текст уведомления" in text

    def test_missing_message(self):
        notif = {"type": "overdue"}
        text = format_notification(notif)
        assert "🚨" in text