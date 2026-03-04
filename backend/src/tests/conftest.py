"""
Глобальные фикстуры и патчи для всего тест-сьюта.

ВАЖНО: переменные окружения устанавливаются ДО любых импортов из приложения,
потому что main.py и session.py читают env на уровне модуля при импорте.
"""
import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ── Env-переменные ────────────────────────────────────────────────────────────
# Должны быть установлены ДО "from main import app" и т.п.
os.environ.setdefault("API_TOKEN",      "test-token-minimum-16chars-xx")
os.environ.setdefault("DATABASE_URL",   "postgresql+asyncpg://test:test@localhost/testdb")
os.environ.setdefault("BOT_WEBHOOK_URL","http://test-bot:8001/webhook")
os.environ.setdefault("MEDIA_UPLOAD_DIR", "/tmp/test_media")  # безопасная tmpdir

import pytest
from httpx import AsyncClient, ASGITransport

# Теперь можно импортировать app
from main import app
from infrastructure.db.session import get_db
from domain.domain_models import BookStatus, NotificationType

# ── Константы ─────────────────────────────────────────────────────────────────
TEST_TOKEN   = "test-token-minimum-16chars-xx"
AUTH_HEADERS = {"X-API-Token": TEST_TOKEN}


# ── Хелперы: фиктивные ORM-объекты ────────────────────────────────────────────

def make_user(
    id: int = 1,
    full_name: str = "Иван Иванов",
    username: str = "ivan",
    is_admin: bool = False,
    created_at: datetime = None,
) -> SimpleNamespace:
    """
    Возвращает объект с атрибутами как у UserTable.
    SimpleNamespace работает с Pydantic from_attributes=True.
    """
    return SimpleNamespace(
        id=id,
        tg_id=id,          # property UserTable.tg_id = self.id
        full_name=full_name,
        username=username,
        is_admin=is_admin,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def make_book(
    id: int = 1,
    title: str = "Война и мир",
    author: str = "Толстой",
    owner_id: int = 1,
    borrower_id: int = None,
    status: BookStatus = BookStatus.AVAILABLE,
    is_deleted: bool = False,
    genre: str = "Роман",
    image_path: str = "books/base_cover.jpg",
    return_due_date: datetime = None,
) -> SimpleNamespace:
    """
    Возвращает объект с атрибутами как у BookTable.
    """
    owner = make_user(id=owner_id)
    borrower = make_user(id=borrower_id) if borrower_id else None
    return SimpleNamespace(
        id=id,
        title=title,
        author=author,
        description=None,
        genre=genre,
        isbn=None,
        image_path=image_path,
        status=status,
        owner_id=owner_id,
        borrower_id=borrower_id,
        return_due_date=return_due_date,
        is_deleted=is_deleted,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        owner=owner,
        borrower=borrower,
    )


def make_history_entry(
    id: int = 1,
    book_id: int = 1,
    user_id: int = 1,
    status_to: BookStatus = BookStatus.AVAILABLE,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        book_id=book_id,
        user_id=user_id,
        status_to=status_to,
        comment="test",
        photo_proof_path=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        action_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db() -> AsyncMock:
    """Мок AsyncSession — заменяет реальную БД во всех тестах."""
    return AsyncMock()


@pytest.fixture
async def client(mock_db):
    """
    Асинхронный HTTP-клиент для тестирования эндпоинтов.

    Патчи при старте:
    - run_background_tasks → бесконечный сон (корректно отменяется)
    - dispose_engine       → no-op async (пул не существует в тестах)
    - image_service.close  → no-op sync  (нет реального executor'а)

    get_db переопределяется через dependency_overrides FastAPI.
    """
    async def mock_run_bg():
        # Имитируем долгую задачу; при shutdown будет asyncio.CancelledError
        await asyncio.sleep(float("inf"))

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    with patch("main.run_background_tasks", mock_run_bg), \
         patch("main.dispose_engine", new_callable=AsyncMock), \
         patch("main.image_service.close", MagicMock()):

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def patch_notifications():
    """
    Блокирует все HTTP-вызовы к боту во всех тестах автоматически.
    autouse=True — применяется без явного указания в параметрах теста.
    """
    with patch(
        "infrastructure.services.background_tasks.NotificationService._send_http_notification",
        new_callable=AsyncMock,
    ):
        yield


@pytest.fixture(autouse=True)
def patch_image_service():
    """
    Блокирует запись/удаление файлов на диск во всех тестах.
    """
    with patch(
        "infrastructure.services.image_service.image_service.process_and_save",
        new_callable=AsyncMock,
        return_value="books/test_image.webp",
    ), patch(
        "infrastructure.services.image_service.image_service.adelete_image",
        new_callable=AsyncMock,
        return_value=True,
    ):
        yield
