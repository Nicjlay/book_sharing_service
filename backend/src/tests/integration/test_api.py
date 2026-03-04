"""
Интеграционные тесты API-эндпоинтов.

Стратегия мокирования:
  - get_db → AsyncMock (из conftest.py)
  - UserRepository, BookRepository → патчим в каждом тесте
  - LibraryService → патчим через dependency_overrides или patch
  - Уведомления и image_service → autouse-патчи из conftest.py

Каждый тест проверяет:
  1. Успешный сценарий (2xx)
  2. Ошибку аутентификации (401) — где применимо
  3. Ключевые ошибочные сценарии (404, 403, 400, 409)
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from domain.domain_models import BookStatus, NotificationType
from domain.schemas import BookRead, UserRead

# conftest.py предоставляет: client, mock_db, make_book, make_user, AUTH_HEADERS
from tests.conftest import AUTH_HEADERS, make_book, make_user, make_history_entry


# ═══════════════════════════════════════════════════════════════════════════════
# GET /health
# ═══════════════════════════════════════════════════════════════════════════════

async def test_health_ok(client):
    """Health check проходит когда БД отвечает."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_db_error(client, mock_db):
    """Health check возвращает 503 при недоступности БД."""
    mock_db.execute.side_effect = Exception("connection lost")
    response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


# ═══════════════════════════════════════════════════════════════════════════════
# Аутентификация — общий тест
# ═══════════════════════════════════════════════════════════════════════════════

async def test_protected_route_without_token_returns_401(client):
    response = await client.get("/books")
    assert response.status_code == 401


async def test_protected_route_with_wrong_token_returns_401(client):
    response = await client.get("/books", headers={"X-API-Token": "wrong-token"})
    assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# POST /users/auth
# ═══════════════════════════════════════════════════════════════════════════════

async def test_auth_user_creates_or_returns_user(client):
    user = make_user()
    with patch("main.UserRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_or_create_user.return_value = user

        response = await client.post(
            "/users/auth",
            json={"tg_id": 1, "full_name": "Иван Иванов", "username": "ivan"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["id"] == 1


async def test_auth_user_requires_token(client):
    response = await client.post("/users/auth", json={"tg_id": 1, "full_name": "test"})
    assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# GET /users
# ═══════════════════════════════════════════════════════════════════════════════

async def test_search_users_returns_page(client):
    users = [make_user(1), make_user(2, full_name="Мария")]
    with patch("main.UserRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.search_users.return_value = users
        repo.count_users.return_value = 2

        response = await client.get("/users", headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


async def test_search_users_short_query_returns_400(client):
    with patch("main.UserRepository"):
        response = await client.get("/users?q=а", headers=AUTH_HEADERS)
    assert response.status_code == 400


async def test_search_users_empty_query_returns_all(client):
    """Пустая строка q='' трактуется как «показать всех»."""
    users = [make_user()]
    with patch("main.UserRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.search_users.return_value = users
        repo.count_users.return_value = 1

        response = await client.get("/users?q=", headers=AUTH_HEADERS)

    assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# POST /users/{tg_id}/set-admin
# ═══════════════════════════════════════════════════════════════════════════════

async def test_set_admin_success(client):
    admin = make_user(id=99, is_admin=True)
    target = make_user(id=1, is_admin=False)

    with patch("main.UserRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.is_admin.return_value = True   # requester is admin
        repo.set_admin.return_value = make_user(id=1, is_admin=True)

        response = await client.post(
            "/users/1/set-admin",
            json={"is_admin": True, "requester_id": 99},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["is_admin"] is True


async def test_set_admin_forbidden_if_not_admin(client):
    with patch("main.UserRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.is_admin.return_value = False  # requester is NOT admin

        response = await client.post(
            "/users/1/set-admin",
            json={"is_admin": True, "requester_id": 2},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 403


async def test_set_admin_cannot_remove_own_rights(client):
    """Администратор не может сам себя лишить прав."""
    with patch("main.UserRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.is_admin.return_value = True

        response = await client.post(
            "/users/1/set-admin",
            json={"is_admin": False, "requester_id": 1},  # requester_id == tg_id
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 400


async def test_set_admin_user_not_found_returns_404(client):
    with patch("main.UserRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.is_admin.return_value = True
        repo.set_admin.return_value = None  # не нашли пользователя

        response = await client.post(
            "/users/999/set-admin",
            json={"is_admin": True, "requester_id": 1},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GET /books/genres
# ═══════════════════════════════════════════════════════════════════════════════

async def test_get_genres(client):
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_all_genres.return_value = ["Роман", "Фантастика"]

        response = await client.get("/books/genres", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert "Роман" in response.json()["genres"]


# ═══════════════════════════════════════════════════════════════════════════════
# GET /books
# ═══════════════════════════════════════════════════════════════════════════════

async def test_list_books(client):
    books = [make_book(id=1), make_book(id=2)]
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_books.return_value = books
        repo.count_books.return_value = 2

        response = await client.get("/books", headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


async def test_list_books_with_status_filter(client):
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_books.return_value = []
        repo.count_books.return_value = 0

        response = await client.get(
            "/books?status=available", headers=AUTH_HEADERS
        )

    assert response.status_code == 200


async def test_list_books_fuzzy_search(client):
    """Запрос с параметром query запускает нечёткий поиск."""
    book = make_book(id=1, title="Война и мир")
    with patch("main.BookRepository") as MockRepo, \
         patch("main._run_fuzzy_search", new_callable=AsyncMock, return_value=[book]):
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_books_lightweight.return_value = [
            {"id": 1, "title": "Война и мир", "author": "Толстой"}
        ]
        repo.get_books_by_ids.return_value = [book]

        response = await client.get(
            "/books?query=война", headers=AUTH_HEADERS
        )

    assert response.status_code == 200
    assert response.json()["total"] == -1  # fuzzy не знает total


async def test_list_books_query_too_short(client):
    with patch("main.BookRepository"):
        response = await client.get("/books?query=а", headers=AUTH_HEADERS)
    assert response.status_code == 400


async def test_list_books_with_user_id(client):
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_my_books.return_value = []
        repo.count_books.return_value = 0

        response = await client.get("/books?user_id=1", headers=AUTH_HEADERS)

    assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# GET /books/{book_id}
# ═══════════════════════════════════════════════════════════════════════════════

async def test_get_book_detail(client):
    book = make_book(id=42)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book

        response = await client.get("/books/42", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["id"] == 42


async def test_get_book_not_found(client):
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = None

        response = await client.get("/books/999", headers=AUTH_HEADERS)

    assert response.status_code == 404


async def test_get_deleted_book_returns_404(client):
    book = make_book(is_deleted=True)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book

        response = await client.get("/books/1", headers=AUTH_HEADERS)

    assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GET /books/{book_id}/history
# ═══════════════════════════════════════════════════════════════════════════════

async def test_get_book_history(client):
    book = make_book()
    entries = [make_history_entry()]
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book
        repo.get_history.return_value = entries
        repo.count_history.return_value = 1

        response = await client.get("/books/1/history", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_get_history_book_not_found(client):
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = None

        response = await client.get("/books/999/history", headers=AUTH_HEADERS)

    assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# POST /books
# ═══════════════════════════════════════════════════════════════════════════════

async def test_create_book(client):
    book = make_book(id=1)
    with patch("main.LibraryService") as MockSvc, \
         patch("main.BookRepository") as MockRepo:
        svc = AsyncMock()
        MockSvc.return_value = svc
        svc.create_book.return_value = 1

        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book

        response = await client.post(
            "/books",
            data={
                "title": "Война и мир",
                "author": "Толстой",
                "genre": "Роман",
                "owner_id": "1",
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 201
    assert response.json()["id"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH /books/{book_id}
# ═══════════════════════════════════════════════════════════════════════════════

async def test_edit_book(client):
    updated_book = make_book(id=1, title="Новое название")
    with patch("main.LibraryService") as MockSvc:
        svc = AsyncMock()
        MockSvc.return_value = svc
        svc.edit_book.return_value = updated_book

        response = await client.patch(
            "/books/1",
            json={"title": "Новое название", "user_id": 1},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Новое название"


# ═══════════════════════════════════════════════════════════════════════════════
# DELETE /books/{book_id}
# ═══════════════════════════════════════════════════════════════════════════════

async def test_delete_book(client):
    with patch("main.LibraryService") as MockSvc:
        svc = AsyncMock()
        MockSvc.return_value = svc
        svc.delete_book.return_value = None

        response = await client.delete(
            "/books/1?user_id=1", headers=AUTH_HEADERS
        )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /media/upload
# ═══════════════════════════════════════════════════════════════════════════════

async def test_upload_media(client):
    with patch("main.image_service.process_and_save",
               new_callable=AsyncMock,
               return_value="books/test.webp"):
        response = await client.post(
            "/media/upload",
            files={"file": ("test.jpg", b"fake-image-data", "image/jpeg")},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert "path" in response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# POST /books/{book_id}/reserve
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reserve_book(client):
    with patch("main.LibraryService") as MockSvc:
        svc = AsyncMock()
        MockSvc.return_value = svc
        svc.request_reservation.return_value = {"status": "reserved_pending_approval"}

        response = await client.post(
            "/books/1/reserve",
            json={"user_id": 2, "days": 14},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "reserved_pending_approval"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /books/{book_id}/approve
# ═══════════════════════════════════════════════════════════════════════════════

async def test_approve_reservation(client):
    book = make_book(id=1, status=BookStatus.BORROWED)
    due_date = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()

    with patch("main.LibraryService") as MockSvc:
        svc = AsyncMock()
        MockSvc.return_value = svc
        svc.approve_reservation.return_value = book

        response = await client.post(
            "/books/1/approve",
            json={"admin_id": 99, "due_date": due_date},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# POST /books/{book_id}/reject
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reject_reservation(client):
    with patch("main.LibraryService") as MockSvc:
        svc = AsyncMock()
        MockSvc.return_value = svc
        svc.reject_reservation.return_value = None

        response = await client.post(
            "/books/1/reject",
            json={"admin_id": 99, "reason": "нет в наличии"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /books/{book_id}/return
# ═══════════════════════════════════════════════════════════════════════════════

async def test_return_book(client):
    with patch("main.LibraryService") as MockSvc:
        svc = AsyncMock()
        MockSvc.return_value = svc
        svc.return_book.return_value = {"status": "returned"}

        response = await client.post(
            "/books/1/return",
            data={"user_id": "2"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "returned"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /books/{book_id}/waitlist
# ═══════════════════════════════════════════════════════════════════════════════

async def test_join_waitlist(client):
    book = make_book(id=1, status=BookStatus.BORROWED, borrower_id=99)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book
        repo.add_to_waitlist.return_value = True

        response = await client.post(
            "/books/1/waitlist",
            json={"user_id": 2},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["added"] is True


async def test_join_waitlist_book_available_returns_400(client):
    """Нельзя встать в очередь если книга свободна."""
    book = make_book(id=1, status=BookStatus.AVAILABLE)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book

        response = await client.post(
            "/books/1/waitlist",
            json={"user_id": 2},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 400


async def test_join_waitlist_own_book_returns_400(client):
    """Нельзя встать в очередь на свою книгу."""
    book = make_book(id=1, status=BookStatus.BORROWED, owner_id=2, borrower_id=99)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book

        response = await client.post(
            "/books/1/waitlist",
            json={"user_id": 2},  # user_id == owner_id
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 400


async def test_join_waitlist_already_borrowed_returns_400(client):
    """Нельзя встать в очередь если уже держишь эту книгу."""
    book = make_book(id=1, status=BookStatus.BORROWED, borrower_id=2)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book

        response = await client.post(
            "/books/1/waitlist",
            json={"user_id": 2},  # user_id == borrower_id
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 400


async def test_join_waitlist_already_reserved_returns_400(client):
    """Нельзя встать в очередь если уже забронировал."""
    book = make_book(id=1, status=BookStatus.RESERVED, borrower_id=2)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book

        response = await client.post(
            "/books/1/waitlist",
            json={"user_id": 2},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 400


async def test_join_waitlist_book_not_found(client):
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = None

        response = await client.post(
            "/books/999/waitlist",
            json={"user_id": 2},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 404


async def test_join_waitlist_already_in_queue(client):
    """add_to_waitlist вернул False → пользователь уже в очереди."""
    book = make_book(id=1, status=BookStatus.BORROWED, borrower_id=99)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book
        repo.add_to_waitlist.return_value = False

        response = await client.post(
            "/books/1/waitlist",
            json={"user_id": 2},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["added"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# DELETE /books/{book_id}/waitlist
# ═══════════════════════════════════════════════════════════════════════════════

async def test_leave_waitlist(client):
    book = make_book(id=1)
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = book
        repo.remove_from_waitlist.return_value = None

        response = await client.delete(
            "/books/1/waitlist?user_id=2", headers=AUTH_HEADERS
        )

    assert response.status_code == 200


async def test_leave_waitlist_book_not_found(client):
    with patch("main.BookRepository") as MockRepo:
        repo = AsyncMock()
        MockRepo.return_value = repo
        repo.get_book_by_id.return_value = None

        response = await client.delete(
            "/books/999/waitlist?user_id=2", headers=AUTH_HEADERS
        )

    assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# POST /bot/webhook
# ═══════════════════════════════════════════════════════════════════════════════

async def test_bot_webhook(client):
    response = await client.post(
        "/bot/webhook",
        json={
            "user_id": 1,
            "type": "book_returned",
            "message": "тест",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "received"


# ═══════════════════════════════════════════════════════════════════════════════
# POST /admin/pending-reservations
# ═══════════════════════════════════════════════════════════════════════════════

async def test_pending_reservations_as_admin(client):
    books = [make_book(id=1, status=BookStatus.RESERVED)]
    with patch("main.UserRepository") as MockUserRepo, \
         patch("main.BookRepository") as MockBookRepo:
        user_repo = AsyncMock()
        MockUserRepo.return_value = user_repo
        user_repo.is_admin.return_value = True

        book_repo = AsyncMock()
        MockBookRepo.return_value = book_repo
        book_repo.get_books.return_value = books
        book_repo.count_books.return_value = 1

        response = await client.post(
            "/admin/pending-reservations",
            json={"requester_id": 99},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_pending_reservations_forbidden_for_non_admin(client):
    with patch("main.UserRepository") as MockUserRepo:
        user_repo = AsyncMock()
        MockUserRepo.return_value = user_repo
        user_repo.is_admin.return_value = False

        response = await client.post(
            "/admin/pending-reservations",
            json={"requester_id": 1},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# GET /notifications/health
# ═══════════════════════════════════════════════════════════════════════════════

async def test_notifications_health(client):
    response = await client.get("/notifications/health", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["mode"] == "push"
    assert response.json()["status"] == "ok"


async def test_notifications_health_requires_token(client):
    response = await client.get("/notifications/health")
    assert response.status_code == 401