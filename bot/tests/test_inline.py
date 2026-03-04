"""
test_inline.py — тесты для keyboards/inline.py

Клавиатуры — функции, которые принимают данные и возвращают InlineKeyboardMarkup.
Тестируем:
1. Какие кнопки присутствуют (по тексту и callback_data)
2. Какие кнопки отсутствуют (условная логика)
3. Граничные случаи (пустые списки, длинные названия)

МЕТОД: извлекаем все кнопки из разметки и проверяем их наличие/отсутствие.
"""
import pytest
from aiogram.types import InlineKeyboardMarkup
from keyboards.inline import (
    MAX_GENRES,
    admin_panel_keyboard,
    book_card_keyboard,
    borrowed_book_actions_keyboard,
    borrowed_books_keyboard,
    cancel_keyboard,
    catalog_filters_keyboard,
    edit_field_keyboard,
    genres_keyboard,
    main_menu_keyboard,
    pagination_keyboard,
    reservation_days_keyboard,
    return_photo_keyboard,
    users_selection_keyboard,
    wizard_confirm_keyboard,
    wizard_skip_photo_keyboard,
)


def get_all_buttons(keyboard: InlineKeyboardMarkup) -> list:
    """Вспомогательная функция: плоский список всех кнопок клавиатуры."""
    return [btn for row in keyboard.inline_keyboard for btn in row]


def get_callback_data(keyboard: InlineKeyboardMarkup) -> set:
    """Множество всех callback_data кнопок."""
    return {btn.callback_data for btn in get_all_buttons(keyboard)}


def get_button_texts(keyboard: InlineKeyboardMarkup) -> list:
    return [btn.text for btn in get_all_buttons(keyboard)]


# ═══════════════════════════════════════════
# main_menu_keyboard
# ═══════════════════════════════════════════

class TestMainMenuKeyboard:
    def test_regular_user_no_admin_button(self):
        kb = main_menu_keyboard(is_admin=False)
        texts = get_button_texts(kb)
        assert "👨‍💼 Админ панель" not in texts

    def test_admin_user_has_admin_button(self):
        kb = main_menu_keyboard(is_admin=True)
        texts = get_button_texts(kb)
        assert "👨‍💼 Админ панель" in texts

    def test_catalog_and_search_always_present(self):
        for is_admin in [True, False]:
            kb = main_menu_keyboard(is_admin=is_admin)
            texts = get_button_texts(kb)
            assert "📖 Каталог" in texts
            assert "🔍 Поиск" in texts


# ═══════════════════════════════════════════
# genres_keyboard
# ═══════════════════════════════════════════

class TestGenresKeyboard:
    def test_genres_shown(self):
        genres = ["Роман", "Фантастика", "История"]
        kb = genres_keyboard(genres)
        texts = get_button_texts(kb)
        assert "Роман" in texts
        assert "Фантастика" in texts

    def test_callback_uses_index_not_name(self):
        """
        КРИТИЧНО: callback_data должен быть 'genre_idx:N', а НЕ 'genre:Название'.
        Кириллические имена превышают лимит Telegram 64 байта.
        """
        genres = ["Современная проза", "Фантастика"]
        kb = genres_keyboard(genres)
        cbs = get_callback_data(kb)
        assert "genre_idx:0" in cbs
        assert "genre_idx:1" in cbs
        # Старый формат НЕ должен быть
        assert not any(cb.startswith("genre:") for cb in cbs if cb)

    def test_max_genres_limit(self):
        genres = [f"Жанр {i}" for i in range(MAX_GENRES + 5)]
        kb = genres_keyboard(genres)
        buttons = get_all_buttons(kb)
        # Кнопок с genre_idx не больше MAX_GENRES
        genre_buttons = [b for b in buttons if b.callback_data and b.callback_data.startswith("genre_idx:")]
        assert len(genre_buttons) == MAX_GENRES

    def test_overflow_notice_shown(self):
        genres = [f"Жанр {i}" for i in range(MAX_GENRES + 1)]
        kb = genres_keyboard(genres)
        texts = get_button_texts(kb)
        assert any("ещё" in t for t in texts)

    def test_back_button_present(self):
        kb = genres_keyboard(["Роман"])
        cbs = get_callback_data(kb)
        assert "catalog" in cbs


# ═══════════════════════════════════════════
# book_card_keyboard
# ═══════════════════════════════════════════

class TestBookCardKeyboard:
    def _book(self, status, owner_id=10, borrower_id=None):
        return {
            "id": 42,
            "status": status,
            "owner_id": owner_id,
            "borrower_id": borrower_id,
        }

    def test_available_non_owner_can_reserve(self):
        book = self._book("available", owner_id=10)
        kb = book_card_keyboard(book, user_id=99)  # не владелец
        cbs = get_callback_data(kb)
        assert "reserve:42" in cbs

    def test_available_owner_cannot_reserve_own_book(self):
        book = self._book("available", owner_id=10)
        kb = book_card_keyboard(book, user_id=10)  # владелец
        cbs = get_callback_data(kb)
        assert "reserve:42" not in cbs

    def test_borrowed_borrower_can_return(self):
        book = self._book("borrowed", owner_id=10, borrower_id=99)
        kb = book_card_keyboard(book, user_id=99)
        cbs = get_callback_data(kb)
        assert "return:42" in cbs

    def test_borrowed_owner_can_return(self):
        book = self._book("borrowed", owner_id=10, borrower_id=99)
        kb = book_card_keyboard(book, user_id=10)
        cbs = get_callback_data(kb)
        assert "return:42" in cbs

    def test_borrowed_stranger_sees_waitlist(self):
        book = self._book("borrowed", owner_id=10, borrower_id=99)
        kb = book_card_keyboard(book, user_id=55, in_waitlist=False)
        cbs = get_callback_data(kb)
        assert "waitlist:42" in cbs
        assert "leave_waitlist:42" not in cbs

    def test_borrowed_in_waitlist_shows_leave_button(self):
        book = self._book("borrowed", owner_id=10, borrower_id=99)
        kb = book_card_keyboard(book, user_id=55, in_waitlist=True)
        cbs = get_callback_data(kb)
        assert "leave_waitlist:42" in cbs
        assert "waitlist:42" not in cbs

    def test_reserved_admin_sees_approve_reject(self):
        book = self._book("reserved", owner_id=10, borrower_id=99)
        kb = book_card_keyboard(book, user_id=1, is_admin=True)
        cbs = get_callback_data(kb)
        assert "approve:42" in cbs
        assert "reject:42" in cbs

    def test_reserved_borrower_sees_waiting_noop(self):
        book = self._book("reserved", owner_id=10, borrower_id=99)
        kb = book_card_keyboard(book, user_id=99)
        texts = get_button_texts(kb)
        assert any("ожидает" in t.lower() for t in texts)

    def test_owner_can_edit_available_book(self):
        book = self._book("available", owner_id=10)
        kb = book_card_keyboard(book, user_id=10)
        cbs = get_callback_data(kb)
        assert "edit:42" in cbs
        assert "delete:42" in cbs

    def test_owner_cannot_edit_borrowed_book(self):
        """Нельзя редактировать/удалять выданную книгу."""
        book = self._book("borrowed", owner_id=10, borrower_id=99)
        kb = book_card_keyboard(book, user_id=10)
        cbs = get_callback_data(kb)
        assert "edit:42" not in cbs
        assert "delete:42" not in cbs

    def test_history_button_always_present(self):
        book = self._book("available")
        kb = book_card_keyboard(book, user_id=99)
        cbs = get_callback_data(kb)
        assert "history:42" in cbs

    def test_back_to_catalog_always_present(self):
        book = self._book("available")
        kb = book_card_keyboard(book, user_id=99)
        cbs = get_callback_data(kb)
        assert "catalog" in cbs


# ═══════════════════════════════════════════
# borrowed_books_keyboard
# ═══════════════════════════════════════════

class TestBorrowedBooksKeyboard:
    def test_shows_books(self):
        books = [
            {"id": 1, "title": "Книга один", "status": "borrowed"},
            {"id": 2, "title": "Книга два", "status": "overdue"},
        ]
        kb = borrowed_books_keyboard(books)
        cbs = get_callback_data(kb)
        assert "borrowed_detail:1" in cbs
        assert "borrowed_detail:2" in cbs

    def test_long_title_truncated(self):
        books = [{"id": 1, "title": "А" * 50, "status": "borrowed"}]
        kb = borrowed_books_keyboard(books)
        texts = get_button_texts(kb)
        # Название книги в кнопке не должно быть длиннее ~30 символов + ".."
        book_text = [t for t in texts if "А" in t][0]
        assert len(book_text) < 50

    def test_empty_list(self):
        kb = borrowed_books_keyboard([])
        cbs = get_callback_data(kb)
        assert "back_to_menu" in cbs

    def test_book_without_title(self):
        books = [{"id": 5, "status": "borrowed"}]  # нет title
        kb = borrowed_books_keyboard(books)  # не должно падать
        cbs = get_callback_data(kb)
        assert "borrowed_detail:5" in cbs


# ═══════════════════════════════════════════
# pagination_keyboard
# ═══════════════════════════════════════════

class TestPaginationKeyboard:
    def test_middle_page_has_both_arrows(self):
        kb = pagination_keyboard(current_page=2, total_pages=5, callback_prefix="page")
        cbs = get_callback_data(kb)
        assert "page:1" in cbs   # ← назад
        assert "page:3" in cbs   # → вперёд

    def test_first_page_no_back_arrow(self):
        kb = pagination_keyboard(current_page=0, total_pages=5, callback_prefix="page")
        cbs = get_callback_data(kb)
        assert "page:-1" not in cbs
        assert "page:1" in cbs

    def test_last_page_no_forward_arrow(self):
        kb = pagination_keyboard(current_page=4, total_pages=5, callback_prefix="page")
        cbs = get_callback_data(kb)
        assert "page:5" not in cbs
        assert "page:3" in cbs


# ═══════════════════════════════════════════
# users_selection_keyboard
# ═══════════════════════════════════════════

class TestUsersSelectionKeyboard:
    def test_users_shown(self):
        users = [{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"}]
        kb = users_selection_keyboard(users)
        cbs = get_callback_data(kb)
        assert "select_owner:1" in cbs
        assert "select_owner:2" in cbs

    def test_user_without_id_skipped(self):
        """Пользователи без id не должны попасть в клавиатуру."""
        users = [{"username": "noid"}, {"id": 5, "username": "valid"}]
        kb = users_selection_keyboard(users)
        cbs = get_callback_data(kb)
        assert "select_owner:5" in cbs
        # Кнопки без id не должны быть (select_owner:0 тоже недопустим)
        assert "select_owner:0" not in cbs

    def test_cancel_button_always_present(self):
        kb = users_selection_keyboard([])
        cbs = get_callback_data(kb)
        assert "wizard_cancel" in cbs

    def test_max_10_users(self):
        users = [{"id": i, "username": f"user{i}"} for i in range(1, 20)]
        kb = users_selection_keyboard(users)
        owner_buttons = [cb for cb in get_callback_data(kb) if cb and cb.startswith("select_owner:")]
        assert len(owner_buttons) <= 10


# ═══════════════════════════════════════════
# Простые клавиатуры (smoke test)
# ═══════════════════════════════════════════

class TestSimpleKeyboards:
    def test_cancel_keyboard(self):
        kb = cancel_keyboard()
        assert "cancel" in get_callback_data(kb)

    def test_reservation_days_keyboard_has_options(self):
        kb = reservation_days_keyboard()
        cbs = get_callback_data(kb)
        assert "days:7" in cbs
        assert "days:14" in cbs
        assert "days:custom" in cbs

    def test_wizard_confirm_keyboard(self):
        kb = wizard_confirm_keyboard()
        cbs = get_callback_data(kb)
        assert "wizard_confirm" in cbs
        assert "wizard_cancel" in cbs

    def test_wizard_skip_photo_keyboard(self):
        kb = wizard_skip_photo_keyboard()
        cbs = get_callback_data(kb)
        assert "skip_photo" in cbs

    def test_edit_field_keyboard(self):
        kb = edit_field_keyboard()
        cbs = get_callback_data(kb)
        assert "edit_field:author" in cbs
        assert "edit_field:title" in cbs
        assert "edit_field:photo" in cbs

    def test_admin_panel_keyboard(self):
        kb = admin_panel_keyboard()
        cbs = get_callback_data(kb)
        assert "admin_reservations" in cbs
        assert "add_book" in cbs

    def test_return_photo_keyboard(self):
        kb = return_photo_keyboard()
        cbs = get_callback_data(kb)
        assert "skip_return_photo" in cbs

    def test_catalog_filters_keyboard(self):
        kb = catalog_filters_keyboard()
        cbs = get_callback_data(kb)
        assert "filter:all" in cbs
        assert "filter:available" in cbs
        assert "filter:genres" in cbs

    def test_borrowed_book_actions(self):
        kb = borrowed_book_actions_keyboard(book_id=99)
        cbs = get_callback_data(kb)
        assert "return:99" in cbs
        assert "history:99" in cbs
