"""
test_webhook.py — тесты для webhook.py (FastAPI эндпоинты)

FastAPI предоставляет TestClient — синхронный HTTP-клиент для тестирования
без запуска реального сервера. Мы делаем HTTP-запросы к нашему приложению
прямо в памяти, без сети.

Что тестируем:
1. /health — простой эндпоинт
2. POST /webhook — авторизация, валидация, маршрутизация уведомлений

ВАЖНО: bot-объект подменяется моком, чтобы реальные сообщения не отправлялись.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

VALID_TOKEN = "test-secret-token"
HEADERS = {"X-API-Token": VALID_TOKEN}


@pytest.fixture
def mock_bot_instance():
    """Мок объекта aiogram Bot."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=2))
    return bot


@pytest.fixture
def client(mock_bot_instance):
    """
    FastAPI TestClient с инициализированным ботом.

    ПРИЧИНА ОШИБКИ с 401: settings — это синглтон из config.py, созданный
    при первом импорте модуля. Его поле api_token может содержать значение
    из настоящего .env файла, а не "test-secret-token" из conftest.
    os.environ.setdefault() не перезаписывает уже существующие переменные,
    а pydantic-settings читает .env файл при создании Settings().

    ФИКС: принудительно подменяем api_token через object.__setattr__,
    который обходит pydantic-валидацию (она блокирует обычное присваивание).
    Восстанавливаем оригинальное значение в finally.
    """
    import webhook as wh
    from config import settings

    original_token = settings.api_token
    # object.__setattr__ нужен потому что pydantic v2 по умолчанию
    # запрещает прямое присваивание полей через model.__setattr__
    object.__setattr__(settings, "api_token", VALID_TOKEN)

    wh.set_bot(mock_bot_instance)
    try:
        with patch.object(wh, "_fetch_photo", new=AsyncMock(return_value=None)):
            yield TestClient(wh.app)
    finally:
        object.__setattr__(settings, "api_token", original_token)


def make_payload(**kwargs) -> dict:
    """Базовый валидный payload уведомления."""
    base = {"user_id": 123, "type": "new_book", "message": "Новая книга добавлена!"}
    base.update(kwargs)
    return base


# ═══════════════════════════════════════════
# /health
# ═══════════════════════════════════════════

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "bot_initialized" in data

    def test_health_bot_initialized_true(self, client):
        resp = client.get("/health")
        assert resp.json()["bot_initialized"] is True


# ═══════════════════════════════════════════
# POST /webhook — авторизация
# ═══════════════════════════════════════════

class TestWebhookAuth:
    def test_no_token_returns_401(self, client):
        resp = client.post(
            "/webhook",
            json=make_payload(),
            # Без заголовка X-API-Token
        )
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.post(
            "/webhook",
            headers={"X-API-Token": "wrong-token"},
            json=make_payload(),
        )
        assert resp.status_code == 401

    def test_valid_token_accepted(self, client):
        resp = client.post("/webhook", headers=HEADERS, json=make_payload())
        assert resp.status_code == 200


# ═══════════════════════════════════════════
# POST /webhook — валидация payload
# ═══════════════════════════════════════════

class TestWebhookValidation:
    def test_invalid_json_returns_422(self, client):
        # ПРИЧИНА ОШИБКИ: в оригинале был несуществующий аргумент headers_extra.
        # TestClient.post() принимает только headers= (один словарь).
        # ФИКС: передаём Content-Type прямо в headers вместе с токеном.
        resp = client.post(
            "/webhook",
            headers={**HEADERS, "Content-Type": "application/json"},
            content=b"not valid json",
        )
        assert resp.status_code in (400, 422)

    def test_missing_required_field_returns_422(self, client):
        # type и message обязательны
        resp = client.post(
            "/webhook",
            headers=HEADERS,
            json={"user_id": 123},  # нет type и message
        )
        assert resp.status_code == 422

    def test_valid_payload_returns_ok(self, client):
        resp = client.post("/webhook", headers=HEADERS, json=make_payload())
        assert resp.json() == {"status": "ok"}


# ═══════════════════════════════════════════
# POST /webhook — маршрутизация уведомлений
# ═══════════════════════════════════════════

class TestWebhookRouting:
    def test_send_to_specific_user(self, client, mock_bot_instance):
        """user_id > 0 → отправляем конкретному пользователю."""
        resp = client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(user_id=12345),
        )
        assert resp.status_code == 200
        mock_bot_instance.send_message.assert_called_once()
        call_kwargs = mock_bot_instance.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 12345

    def test_send_to_all_admins(self, client, mock_bot_instance):
        """user_id == -1 → отправляем всем администраторам (111, 222, 333 из conftest)."""
        resp = client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(user_id=-1),
        )
        assert resp.status_code == 200
        # send_message должен быть вызван для каждого из 3 администраторов
        assert mock_bot_instance.send_message.call_count == 3

    def test_send_to_group(self, client, mock_bot_instance):
        """user_id == 0 → отправляем в группу (group_chat_id из env = -100123456)."""
        resp = client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(user_id=0),
        )
        assert resp.status_code == 200
        mock_bot_instance.send_message.assert_called_once()
        call_kwargs = mock_bot_instance.send_message.call_args[1]
        assert call_kwargs["chat_id"] == -100123456

    def test_admin_reservation_request_has_keyboard(self, client, mock_bot_instance):
        """Тип 'admin_reservation_request' → добавляются кнопки быстрого действия."""
        resp = client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(
                user_id=-1,
                type="admin_reservation_request",
                book_id=42,
            ),
        )
        assert resp.status_code == 200
        # Каждый вызов send_message должен получить reply_markup
        for call in mock_bot_instance.send_message.call_args_list:
            assert call[1].get("reply_markup") is not None

    def test_payload_too_large_returns_413(self, client):
        """Content-Length > 1MB → 413 Payload Too Large."""
        big_content = b"x" * (1024 * 1024 + 1)
        resp = client.post(
            "/webhook",
            headers={**HEADERS, "Content-Length": str(len(big_content))},
            content=big_content,
        )
        assert resp.status_code == 413