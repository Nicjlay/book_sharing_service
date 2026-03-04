"""
conftest.py — общие фикстуры для всех тестов.

Запуск всех тестов:
    pytest tests/ -v --cov=. --cov-report=term-missing

Установка зависимостей для тестов:
    pip install pytest pytest-asyncio pytest-cov httpx
    (основные зависимости проекта тоже нужны: aiogram, pydantic-settings, aiohttp, fastapi, uvicorn)
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Переменные окружения нужно выставить ДО того, как импортируется config.py.
# Pydantic-Settings читает env при создании Settings(), т.е. при первом импорте.
os.environ.setdefault("BOT_TOKEN", "123456789:AABBCCDDEEFFaabbccddeeff1234567890AB")
os.environ.setdefault("API_URL", "http://testserver")
os.environ.setdefault("API_TOKEN", "test-secret-token")
os.environ.setdefault("ADMIN_USER_IDS", "111,222,333")
os.environ.setdefault("GROUP_CHAT_ID", "-100123456")
os.environ.setdefault("WEBHOOK_PATH", "/webhook")

# ── Добавляем корень проекта в sys.path, чтобы работали абсолютные импорты
# (from utils.formatters import ..., from api.client import ...)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ─────────────────────────────────────────────
# Вспомогательные фикстуры — используются во многих тестах
# ─────────────────────────────────────────────

@pytest.fixture
def sample_book():
    """Типичная книга в статусе 'available'."""
    return {
        "id": 42,
        "title": "Мастер и Маргарита",
        "author": "Булгаков Михаил",
        "status": "available",
        "owner_id": 111,
        "owner_username": "bulgakov_fan",
        "owner_full_name": "Иван Иванов",
        "genre": "Роман",
        "description": "Культовый роман о визите дьявола в советскую Москву.",
        "image_path": "covers/42.jpg",
    }


@pytest.fixture
def borrowed_book():
    """Книга в статусе 'borrowed' с датой возврата."""
    return {
        "id": 7,
        "title": "1984",
        "author": "Оруэлл Джордж",
        "status": "borrowed",
        "owner_id": 111,
        "owner_username": "owner",
        "borrower_id": 222,
        "borrower_username": "reader_user",
        "return_due_date": "2025-12-31T00:00:00Z",
        "genre": "Антиутопия",
    }


@pytest.fixture
def overdue_book():
    """Книга в статусе 'overdue'."""
    return {
        "id": 99,
        "title": "Преступление и наказание",
        "author": "Достоевский Федор",
        "status": "overdue",
        "owner_id": 111,
        "borrower_id": 333,
        "borrower_full_name": "Петр Петров",
        "return_due_date": "2024-01-01T00:00:00",
    }


@pytest.fixture
def mock_bot():
    """Мок объекта aiogram Bot."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    bot.send_photo = AsyncMock(return_value=MagicMock())
    return bot


@pytest.fixture
def mock_message():
    """Мок объекта aiogram Message."""
    msg = MagicMock()
    msg.photo = None
    msg.document = None
    msg.sticker = None
    msg.video = None
    msg.edit_text = AsyncMock()
    msg.edit_caption = AsyncMock()
    msg.delete = AsyncMock()
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=999, full_name="Test User", username="testuser")
    return msg


@pytest.fixture
def mock_callback(mock_message):
    """Мок объекта aiogram CallbackQuery."""
    cb = MagicMock()
    cb.message = mock_message
    cb.from_user = MagicMock(id=999, full_name="Test User", username="testuser")
    cb.answer = AsyncMock()
    cb.data = "test"
    return cb
