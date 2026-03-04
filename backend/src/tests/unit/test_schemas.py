"""
Тесты Pydantic-схем.

Проверяем все валидаторы: нормализацию, отклонение невалидных данных,
граничные случаи (None, пустые строки, спецсимволы).
"""
import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from domain.schemas import (
    BookCreate, BookUpdate, BookRead, UserAuthRequest,
    ReservationRequest, ApproveRequest, RejectRequest,
    NotificationPayload, SetAdminRequest,
)
from domain.domain_models import BookStatus, NotificationType


# ── BookCreate / BookBase validators ─────────────────────────────────────────

class TestBookCreateValidation:

    def _valid(self, **overrides):
        data = dict(
            title="Война и мир",
            author="Лев Толстой",
            owner_id=123,
            genre="Роман",
        )
        data.update(overrides)
        return BookCreate(**data)

    # title
    def test_title_stripped(self):
        book = self._valid(title="  Война и мир  ")
        assert book.title == "Война и мир"

    def test_title_whitespace_collapsed(self):
        book = self._valid(title="Война  и  мир")
        assert book.title == "Война и мир"

    def test_title_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(title="")

    def test_title_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(title="а" * 201)

    # author
    def test_author_stripped(self):
        book = self._valid(author="  Толстой  ")
        assert book.author == "Толстой"

    def test_author_too_short_raises(self):
        with pytest.raises(ValidationError, match="слишком короткое"):
            self._valid(author="Т")

    def test_author_whitespace_collapsed(self):
        book = self._valid(author="Лев  Толстой")
        assert book.author == "Лев Толстой"

    # description
    def test_description_none_stays_none(self):
        book = self._valid(description=None)
        assert book.description is None

    def test_description_empty_string_becomes_none(self):
        book = self._valid(description="   ")
        assert book.description is None

    def test_description_stripped(self):
        book = self._valid(description="  описание  ")
        assert book.description == "описание"

    # genre
    def test_genre_none_becomes_default(self):
        book = self._valid(genre=None)
        assert book.genre == "Другое"

    def test_genre_empty_becomes_default(self):
        book = self._valid(genre="")
        assert book.genre == "Другое"

    def test_genre_preserved(self):
        book = self._valid(genre="Фантастика")
        assert book.genre == "Фантастика"

    # isbn
    def test_isbn_none_allowed(self):
        book = self._valid(isbn=None)
        assert book.isbn is None

    def test_isbn10_valid(self):
        book = self._valid(isbn="0-306-40615-2")
        assert book.isbn is not None

    def test_isbn13_valid(self):
        book = self._valid(isbn="978-3-16-148410-0")
        assert book.isbn is not None

    def test_isbn_invalid_raises(self):
        with pytest.raises(ValidationError, match="ISBN"):
            self._valid(isbn="not-an-isbn")

    # image_path
    def test_image_path_valid(self):
        book = self._valid(image_path="books/cover.webp")
        assert book.image_path == "books/cover.webp"

    def test_image_path_none_allowed(self):
        book = self._valid(image_path=None)
        assert book.image_path is None

    def test_image_path_traversal_rejected(self):
        with pytest.raises(ValidationError, match="Недопустимый"):
            self._valid(image_path="../etc/passwd")

    def test_image_path_absolute_rejected(self):
        with pytest.raises(ValidationError, match="Недопустимый"):
            self._valid(image_path="/etc/passwd")

    def test_image_path_backslash_normalized(self):
        book = self._valid(image_path="books\\cover.webp")
        assert "\\" not in book.image_path

    # owner_id
    def test_owner_id_must_be_positive(self):
        with pytest.raises(ValidationError):
            self._valid(owner_id=0)

    def test_owner_id_negative_raises(self):
        with pytest.raises(ValidationError):
            self._valid(owner_id=-1)


# ── BookUpdate (PATCH semantics) ──────────────────────────────────────────────

class TestBookUpdateValidation:

    def test_all_fields_optional(self):
        """PATCH: все поля опциональны — можно передать пустой объект."""
        update = BookUpdate()
        assert update.title is None
        assert update.author is None

    def test_genre_none_stays_none(self):
        """При PATCH, None означает «очистить поле», а не «поставить Другое»."""
        update = BookUpdate(genre=None)
        assert update.genre is None

    def test_genre_empty_string_becomes_default(self):
        update = BookUpdate(genre="")
        assert update.genre == "Другое"

    def test_title_normalized(self):
        update = BookUpdate(title="  книга  ")
        assert update.title == "книга"

    def test_author_none_stays_none(self):
        update = BookUpdate(author=None)
        assert update.author is None


# ── ApproveRequest ────────────────────────────────────────────────────────────

class TestApproveRequest:
    def _future(self, days=10):
        return datetime.now(timezone.utc) + timedelta(days=days)

    def test_valid(self):
        req = ApproveRequest(admin_id=1, due_date=self._future())
        assert req.admin_id == 1

    def test_past_due_date_raises(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        with pytest.raises(ValidationError, match="будущем"):
            ApproveRequest(admin_id=1, due_date=past)

    def test_too_far_future_raises(self):
        far = datetime.now(timezone.utc) + timedelta(days=800)
        with pytest.raises(ValidationError, match="730"):
            ApproveRequest(admin_id=1, due_date=far)

    def test_naive_datetime_gets_utc(self):
        """Naive datetime без tzinfo → автоматически привязывается к UTC."""
        naive = datetime.now() + timedelta(days=5)
        req = ApproveRequest(admin_id=1, due_date=naive)
        assert req.due_date.tzinfo is not None


# ── NotificationPayload ───────────────────────────────────────────────────────

class TestNotificationPayload:

    def test_valid_user_notification(self):
        payload = NotificationPayload(
            user_id=123,
            type=NotificationType.BOOK_RETURNED,
            message="тест",
        )
        assert payload.user_id == 123

    def test_broadcast_user_id_zero(self):
        payload = NotificationPayload(
            user_id=0,
            type=NotificationType.NEW_BOOK,
            message="новая книга",
        )
        assert payload.user_id == 0

    def test_all_admins_user_id_minus_one(self):
        payload = NotificationPayload(
            user_id=-1,
            type=NotificationType.OVERDUE,
            message="просрочка",
        )
        assert payload.user_id == -1

    def test_negative_user_id_below_minus_one_raises(self):
        with pytest.raises(ValidationError):
            NotificationPayload(user_id=-2, type=NotificationType.OVERDUE, message="x")

    def test_book_id_must_be_positive(self):
        with pytest.raises(ValidationError):
            NotificationPayload(
                user_id=1,
                type=NotificationType.NEW_BOOK,
                message="x",
                book_id=0,
            )

    def test_book_id_none_allowed(self):
        payload = NotificationPayload(
            user_id=1,
            type=NotificationType.NEW_BOOK,
            message="x",
            book_id=None,
        )
        assert payload.book_id is None

    def test_message_too_long_raises(self):
        with pytest.raises(ValidationError):
            NotificationPayload(
                user_id=1,
                type=NotificationType.NEW_BOOK,
                message="x" * 4097,
            )


# ── UserAuthRequest ───────────────────────────────────────────────────────────

class TestUserAuthRequest:

    def test_valid(self):
        req = UserAuthRequest(tg_id=123456, full_name="Иван Иванов")
        assert req.tg_id == 123456

    def test_tg_id_zero_raises(self):
        with pytest.raises(ValidationError):
            UserAuthRequest(tg_id=0, full_name="test")

    def test_username_empty_becomes_none(self):
        req = UserAuthRequest(tg_id=1, full_name="test", username="")
        assert req.username is None

    def test_username_whitespace_becomes_none(self):
        req = UserAuthRequest(tg_id=1, full_name="test", username="   ")
        assert req.username is None

    def test_username_stripped(self):
        req = UserAuthRequest(tg_id=1, full_name="test", username="  ivan  ")
        assert req.username == "ivan"


# ── ReservationRequest ────────────────────────────────────────────────────────

class TestReservationRequest:

    def test_valid_default_days(self):
        req = ReservationRequest(user_id=1)
        assert req.days == 14

    def test_custom_days(self):
        req = ReservationRequest(user_id=1, days=30)
        assert req.days == 30

    def test_days_too_small_raises(self):
        with pytest.raises(ValidationError):
            ReservationRequest(user_id=1, days=0)

    def test_days_too_large_raises(self):
        with pytest.raises(ValidationError):
            ReservationRequest(user_id=1, days=91)


# ── SetAdminRequest ───────────────────────────────────────────────────────────

class TestSetAdminRequest:

    def test_valid(self):
        req = SetAdminRequest(is_admin=True, requester_id=1)
        assert req.is_admin is True

    def test_requester_id_zero_raises(self):
        with pytest.raises(ValidationError):
            SetAdminRequest(is_admin=True, requester_id=0)