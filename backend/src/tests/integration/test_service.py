"""
Тесты сервисного слоя LibraryService.

Стратегия: мокируем репозитории и внешние сервисы (уведомления, изображения).
Проверяем бизнес-логику: права доступа, переходы статусов, race-condition handling.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from infrastructure.services.book_service import LibraryService
from domain.domain_models import BookStatus
from domain.schemas import BookCreate, BookUpdate

from tests.conftest import make_book, make_user


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_book_repo():
    return AsyncMock()


@pytest.fixture
def mock_user_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_session, mock_book_repo, mock_user_repo):
    """LibraryService с мокнутыми репозиториями."""
    svc = LibraryService(mock_session)
    svc.book_repo = mock_book_repo
    svc.user_repo = mock_user_repo
    return svc


# ══════════════════════════════════════════════════════════════════════════════
# create_book
# ══════════════════════════════════════════════════════════════════════════════

class TestCreateBook:

    def _book_in(self, **kwargs):
        data = dict(
            title="Война и мир",
            author="Толстой",
            owner_id=1,
            genre="Роман",
        )
        data.update(kwargs)
        return BookCreate(**data)

    async def test_creates_book_without_photo(self, service, mock_book_repo, mock_user_repo):
        owner = make_user(id=1)
        book  = make_book(id=42)
        mock_user_repo.get_by_id.return_value = owner
        mock_book_repo.add_book.return_value   = book
        mock_book_repo.log_history             = AsyncMock()

        result = await service.create_book(self._book_in())

        assert result == 42
        mock_book_repo.add_book.assert_called_once()

    async def test_raises_404_if_owner_not_found(self, service, mock_user_repo):
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.create_book(self._book_in())

        assert exc.value.status_code == 404

    async def test_uses_image_path_if_provided(self, service, mock_book_repo, mock_user_repo):
        owner = make_user(id=1)
        book  = make_book(id=1, image_path="books/my.webp")
        mock_user_repo.get_by_id.return_value = owner
        mock_book_repo.add_book.return_value  = book
        mock_book_repo.log_history            = AsyncMock()

        await service.create_book(self._book_in(), image_path="books/my.webp")

        call_kwargs = mock_book_repo.add_book.call_args
        assert call_kwargs[1]["image_path"] == "books/my.webp"

    async def test_uses_default_cover_when_no_image(self, service, mock_book_repo, mock_user_repo):
        owner = make_user(id=1)
        book  = make_book(id=1)
        mock_user_repo.get_by_id.return_value = owner
        mock_book_repo.add_book.return_value  = book
        mock_book_repo.log_history            = AsyncMock()

        await service.create_book(self._book_in())

        call_kwargs = mock_book_repo.add_book.call_args
        assert call_kwargs[1]["image_path"] == "books/base_cover.jpg"

    async def test_notification_failure_is_non_fatal(self, service, mock_book_repo, mock_user_repo):
        """Ошибка уведомления не прерывает создание книги."""
        owner = make_user(id=1)
        book  = make_book(id=1)
        mock_user_repo.get_by_id.return_value = owner
        mock_book_repo.add_book.return_value  = book
        mock_book_repo.log_history            = AsyncMock()

        with patch(
            "infrastructure.services.book_service.notification_service.notify_new_book",
            side_effect=Exception("bot down"),
        ):
            result = await service.create_book(self._book_in())

        assert result == 1  # книга всё равно создана


# ══════════════════════════════════════════════════════════════════════════════
# edit_book
# ══════════════════════════════════════════════════════════════════════════════

class TestEditBook:

    async def test_owner_can_edit(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, owner_id=5)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = False
        mock_book_repo.update_book                  = AsyncMock()
        mock_book_repo.log_history                  = AsyncMock()

        update = BookUpdate(title="Новое название")
        await service.edit_book(book_id=1, user_id=5, update_data=update)

        mock_book_repo.update_book.assert_called_once()

    async def test_admin_can_edit_any_book(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, owner_id=99)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = True
        mock_book_repo.update_book                  = AsyncMock()
        mock_book_repo.log_history                  = AsyncMock()

        update = BookUpdate(title="Изменено")
        await service.edit_book(book_id=1, user_id=1, update_data=update)

        mock_book_repo.update_book.assert_called_once()

    async def test_non_owner_non_admin_raises_403(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, owner_id=99)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = False

        with pytest.raises(HTTPException) as exc:
            await service.edit_book(1, user_id=1, update_data=BookUpdate(title="X"))

        assert exc.value.status_code == 403

    async def test_deleted_book_raises_404(self, service, mock_book_repo):
        mock_book_repo.get_book_by_id.return_value = make_book(is_deleted=True)

        with pytest.raises(HTTPException) as exc:
            await service.edit_book(1, user_id=1, update_data=BookUpdate())

        assert exc.value.status_code == 404

    async def test_not_found_raises_404(self, service, mock_book_repo):
        mock_book_repo.get_book_by_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.edit_book(999, user_id=1, update_data=BookUpdate())

        assert exc.value.status_code == 404

    async def test_old_image_deleted_when_image_changed(
        self, service, mock_book_repo, mock_user_repo
    ):
        """При смене image_path старый файл удаляется."""
        book = make_book(id=1, owner_id=1, image_path="books/old.webp")
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = False
        mock_book_repo.update_book                  = AsyncMock()
        mock_book_repo.log_history                  = AsyncMock()

        update = BookUpdate(image_path="books/new.webp")
        with patch(
            "infrastructure.services.book_service.image_service.adelete_image",
            new_callable=AsyncMock,
        ) as mock_delete:
            await service.edit_book(1, user_id=1, update_data=update)

        mock_delete.assert_called_once_with("books/old.webp")


# ══════════════════════════════════════════════════════════════════════════════
# delete_book
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteBook:

    async def test_owner_can_delete(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, owner_id=1, status=BookStatus.AVAILABLE)
        mock_book_repo.get_book_by_id.return_value  = book
        mock_user_repo.is_admin.return_value         = False
        mock_book_repo.get_waitlist_users.return_value = []
        mock_book_repo.log_history                   = AsyncMock()
        mock_book_repo.soft_delete_book              = AsyncMock()
        mock_book_repo.clear_waitlist                = AsyncMock()

        await service.delete_book(1, user_id=1)

        mock_book_repo.soft_delete_book.assert_called_once_with(1)

    async def test_borrowed_book_cannot_be_deleted(self, service, mock_book_repo, mock_user_repo):
        book = make_book(status=BookStatus.BORROWED)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = True  # даже админ не может

        with pytest.raises(HTTPException) as exc:
            await service.delete_book(1, user_id=99)

        assert exc.value.status_code == 400

    async def test_reserved_book_cannot_be_deleted(self, service, mock_book_repo, mock_user_repo):
        book = make_book(status=BookStatus.RESERVED)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = True

        with pytest.raises(HTTPException) as exc:
            await service.delete_book(1, user_id=99)

        assert exc.value.status_code == 400

    async def test_waitlist_users_notified_on_delete(
        self, service, mock_book_repo, mock_user_repo
    ):
        book = make_book(id=1, status=BookStatus.AVAILABLE)
        mock_book_repo.get_book_by_id.return_value     = book
        mock_user_repo.is_admin.return_value            = False
        mock_book_repo.get_waitlist_users.return_value  = [10, 20]
        mock_book_repo.log_history                      = AsyncMock()
        mock_book_repo.soft_delete_book                 = AsyncMock()
        mock_book_repo.clear_waitlist                   = AsyncMock()

        with patch(
            "infrastructure.services.book_service.notification_service"
            ".notify_book_deleted_from_waitlist",
            new_callable=AsyncMock,
        ) as mock_notify:
            await service.delete_book(1, user_id=1)

        assert mock_notify.call_count == 2

    async def test_non_owner_non_admin_cannot_delete(
        self, service, mock_book_repo, mock_user_repo
    ):
        book = make_book(owner_id=99, status=BookStatus.AVAILABLE)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = False

        with pytest.raises(HTTPException) as exc:
            await service.delete_book(1, user_id=1)

        assert exc.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# request_reservation
# ══════════════════════════════════════════════════════════════════════════════

class TestRequestReservation:

    async def test_successful_reservation(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, status=BookStatus.AVAILABLE, owner_id=99)
        mock_book_repo.get_book_by_id.return_value    = book
        mock_book_repo.try_reserve.return_value       = True
        mock_book_repo.count_active_by_user.return_value = 0
        mock_book_repo.log_history                    = AsyncMock()
        mock_user_repo.get_by_id.return_value         = make_user(id=2)

        result = await service.request_reservation(1, user_id=2, days=14)

        assert result["status"] == "reserved_pending_approval"

    async def test_own_book_raises_400(self, service, mock_book_repo):
        book = make_book(id=1, owner_id=1, status=BookStatus.AVAILABLE)
        mock_book_repo.get_book_by_id.return_value = book

        with pytest.raises(HTTPException) as exc:
            await service.request_reservation(1, user_id=1, days=14)

        assert exc.value.status_code == 400

    async def test_busy_book_raises_409(self, service, mock_book_repo):
        book = make_book(id=1, status=BookStatus.BORROWED, owner_id=99)
        mock_book_repo.get_book_by_id.return_value = book

        with pytest.raises(HTTPException) as exc:
            await service.request_reservation(1, user_id=2, days=14)

        assert exc.value.status_code == 409

    async def test_limit_exceeded_raises_409(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, status=BookStatus.AVAILABLE, owner_id=99)
        mock_book_repo.get_book_by_id.return_value    = book
        mock_book_repo.try_reserve.return_value       = False
        mock_book_repo.count_active_by_user.return_value = 5  # лимит исчерпан

        with pytest.raises(HTTPException) as exc:
            await service.request_reservation(1, user_id=2, days=14)

        assert exc.value.status_code == 409

    async def test_race_condition_rollback(self, service, mock_book_repo, mock_user_repo):
        """Race condition: try_reserve прошёл, но пост-проверка показала превышение."""
        book = make_book(id=1, status=BookStatus.AVAILABLE, owner_id=99)
        mock_book_repo.get_book_by_id.return_value    = book
        mock_book_repo.try_reserve.return_value       = True
        # Первый call: пост-проверка после try_reserve — превышение
        # Второй call: лимит чтобы не попасть во вторую ветку 409
        mock_book_repo.count_active_by_user.return_value = 6
        mock_book_repo.update_status.return_value     = True
        mock_book_repo.log_history                    = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await service.request_reservation(1, user_id=2, days=14)

        assert exc.value.status_code == 409
        mock_book_repo.update_status.assert_called()  # откат выполнен


# ══════════════════════════════════════════════════════════════════════════════
# approve_reservation
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveReservation:

    def _due_date(self):
        return datetime.now(timezone.utc) + timedelta(days=14)

    async def test_admin_approves(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, status=BookStatus.RESERVED, borrower_id=2)
        mock_user_repo.is_admin.return_value       = True
        mock_book_repo.get_book_by_id.return_value = book
        mock_book_repo.update_status.return_value  = True
        mock_book_repo.log_history                 = AsyncMock()

        result = await service.approve_reservation(1, admin_id=99, due_date=self._due_date())
        assert result is not None

    async def test_non_admin_raises_403(self, service, mock_user_repo):
        mock_user_repo.is_admin.return_value = False

        with pytest.raises(HTTPException) as exc:
            await service.approve_reservation(1, admin_id=1, due_date=self._due_date())

        assert exc.value.status_code == 403

    async def test_book_not_reserved_raises_400(self, service, mock_book_repo, mock_user_repo):
        book = make_book(status=BookStatus.AVAILABLE)
        mock_user_repo.is_admin.return_value       = True
        mock_book_repo.get_book_by_id.return_value = book

        with pytest.raises(HTTPException) as exc:
            await service.approve_reservation(1, admin_id=99, due_date=self._due_date())

        assert exc.value.status_code == 400

    async def test_missing_borrower_raises_422(self, service, mock_book_repo, mock_user_repo):
        """borrower_id=None при статусе RESERVED — data inconsistency."""
        book = make_book(status=BookStatus.RESERVED, borrower_id=None)
        mock_user_repo.is_admin.return_value       = True
        mock_book_repo.get_book_by_id.return_value = book

        with pytest.raises(HTTPException) as exc:
            await service.approve_reservation(1, admin_id=99, due_date=self._due_date())

        assert exc.value.status_code == 422

    async def test_concurrent_approval_raises_409(self, service, mock_book_repo, mock_user_repo):
        """update_status вернул False → другой админ уже одобрил."""
        book = make_book(status=BookStatus.RESERVED, borrower_id=2)
        mock_user_repo.is_admin.return_value       = True
        mock_book_repo.get_book_by_id.return_value = book
        mock_book_repo.update_status.return_value  = False

        with pytest.raises(HTTPException) as exc:
            await service.approve_reservation(1, admin_id=99, due_date=self._due_date())

        assert exc.value.status_code == 409


# ══════════════════════════════════════════════════════════════════════════════
# reject_reservation
# ══════════════════════════════════════════════════════════════════════════════

class TestRejectReservation:

    async def test_admin_rejects(self, service, mock_book_repo, mock_user_repo):
        book = make_book(status=BookStatus.RESERVED, borrower_id=2)
        mock_user_repo.is_admin.return_value            = True
        mock_book_repo.get_book_by_id.return_value      = book
        mock_book_repo.update_status.return_value       = True
        mock_book_repo.log_history                      = AsyncMock()
        mock_book_repo.pop_first_waiter.return_value    = None

        await service.reject_reservation(1, admin_id=99, reason="нет книги")
        mock_book_repo.update_status.assert_called_once()

    async def test_notifies_next_waiter_after_reject(
        self, service, mock_book_repo, mock_user_repo
    ):
        """После отклонения первый из очереди получает уведомление."""
        book = make_book(status=BookStatus.RESERVED, borrower_id=2)
        mock_user_repo.is_admin.return_value            = True
        mock_book_repo.get_book_by_id.return_value      = book
        mock_book_repo.update_status.return_value       = True
        mock_book_repo.log_history                      = AsyncMock()
        mock_book_repo.pop_first_waiter.return_value    = 10  # следующий в очереди
        mock_user_repo.exists.return_value              = True

        with patch(
            "infrastructure.services.book_service.notification_service"
            ".notify_waitlist_available",
            new_callable=AsyncMock,
        ) as mock_notify:
            await service.reject_reservation(1, admin_id=99, reason="нет")

        mock_notify.assert_called_once()

    async def test_skips_notification_if_waiter_deleted(
        self, service, mock_book_repo, mock_user_repo
    ):
        """Пользователь удалён — уведомление не отправляется."""
        book = make_book(status=BookStatus.RESERVED, borrower_id=2)
        mock_user_repo.is_admin.return_value            = True
        mock_book_repo.get_book_by_id.return_value      = book
        mock_book_repo.update_status.return_value       = True
        mock_book_repo.log_history                      = AsyncMock()
        mock_book_repo.pop_first_waiter.return_value    = 10
        mock_user_repo.exists.return_value              = False  # удалён

        with patch(
            "infrastructure.services.book_service.notification_service"
            ".notify_waitlist_available",
            new_callable=AsyncMock,
        ) as mock_notify:
            await service.reject_reservation(1, admin_id=99, reason="нет")

        mock_notify.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# return_book
# ══════════════════════════════════════════════════════════════════════════════

class TestReturnBook:

    async def test_borrower_can_return(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, status=BookStatus.BORROWED, borrower_id=2, owner_id=99)
        mock_book_repo.get_book_by_id.return_value   = book
        mock_user_repo.is_admin.return_value         = False
        mock_book_repo.update_status.return_value    = True
        mock_book_repo.log_history                   = AsyncMock()
        mock_book_repo.pop_first_waiter.return_value = None
        mock_user_repo.get_by_id.return_value        = make_user(id=2)

        result = await service.return_book(1, user_id=2)
        assert result["status"] == "returned"

    async def test_owner_can_return(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, status=BookStatus.BORROWED, borrower_id=2, owner_id=1)
        mock_book_repo.get_book_by_id.return_value   = book
        mock_user_repo.is_admin.return_value         = False
        mock_book_repo.update_status.return_value    = True
        mock_book_repo.log_history                   = AsyncMock()
        mock_book_repo.pop_first_waiter.return_value = None

        result = await service.return_book(1, user_id=1)  # owner_id == 1
        assert result["status"] == "returned"

    async def test_overdue_book_can_be_returned(self, service, mock_book_repo, mock_user_repo):
        book = make_book(id=1, status=BookStatus.OVERDUE, borrower_id=2, owner_id=99)
        mock_book_repo.get_book_by_id.return_value   = book
        mock_user_repo.is_admin.return_value         = False
        mock_book_repo.update_status.return_value    = True
        mock_book_repo.log_history                   = AsyncMock()
        mock_book_repo.pop_first_waiter.return_value = None
        mock_user_repo.get_by_id.return_value        = make_user(id=2)

        result = await service.return_book(1, user_id=2)
        assert result["status"] == "returned"

    async def test_available_book_raises_400(self, service, mock_book_repo, mock_user_repo):
        book = make_book(status=BookStatus.AVAILABLE)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = False

        with pytest.raises(HTTPException) as exc:
            await service.return_book(1, user_id=2)

        assert exc.value.status_code == 400

    async def test_stranger_cannot_return(self, service, mock_book_repo, mock_user_repo):
        """Ни заёмщик, ни владелец, ни админ — 403."""
        book = make_book(status=BookStatus.BORROWED, borrower_id=2, owner_id=99)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = False

        with pytest.raises(HTTPException) as exc:
            await service.return_book(1, user_id=55)  # посторонний

        assert exc.value.status_code == 403

    async def test_concurrent_return_raises_409(self, service, mock_book_repo, mock_user_repo):
        """update_status вернул False → параллельный запрос уже вернул книгу."""
        book = make_book(status=BookStatus.BORROWED, borrower_id=2, owner_id=99)
        mock_book_repo.get_book_by_id.return_value = book
        mock_user_repo.is_admin.return_value        = False
        mock_book_repo.update_status.return_value  = False

        with pytest.raises(HTTPException) as exc:
            await service.return_book(1, user_id=2)

        assert exc.value.status_code == 409

    async def test_notifies_next_waiter_on_return(
        self, service, mock_book_repo, mock_user_repo
    ):
        book = make_book(id=1, status=BookStatus.BORROWED, borrower_id=2, owner_id=99)
        mock_book_repo.get_book_by_id.return_value   = book
        mock_user_repo.is_admin.return_value         = False
        mock_book_repo.update_status.return_value    = True
        mock_book_repo.log_history                   = AsyncMock()
        mock_book_repo.pop_first_waiter.return_value = 10
        mock_user_repo.exists.return_value           = True
        mock_user_repo.get_by_id.return_value        = make_user(id=2)

        with patch(
            "infrastructure.services.book_service.notification_service"
            ".notify_waitlist_available",
            new_callable=AsyncMock,
        ) as mock_notify:
            await service.return_book(1, user_id=2)

        mock_notify.assert_called_once()