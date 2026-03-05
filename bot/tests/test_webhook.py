"""
test_webhook.py — тесты для webhook.py (FastAPI эндпоинты)

ПОЧЕМУ НЕ TestClient:
  starlette==0.35.1 внутри вызывает httpx.Client(app=...).
  В httpx >= 0.20 аргумент app= удалён → TypeError при создании TestClient.
  Это конфликт версий в requirements.txt проекта, не наша ошибка.

СОВРЕМЕННЫЙ СПОСОБ — httpx.AsyncClient + ASGITransport:
  AsyncClient(transport=ASGITransport(app=wh.app), base_url="http://testserver")
  Работает с любым httpx >= 0.20, не зависит от версии starlette.
  Тесты становятся async — используем pytest-asyncio (уже стоит).
"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

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
async def client(mock_bot_instance):
    """
    Async HTTP-клиент, общающийся с FastAPI-приложением напрямую в памяти.

    ASGITransport(app=wh.app) — это "транспортный слой": вместо настоящей
    сети он вызывает ASGI-интерфейс приложения напрямую. Никакого сервера,
    никаких портов — всё происходит внутри одного процесса.

    Токен патчим через object.__setattr__ — pydantic v2 блокирует обычное
    присваивание полей у Settings, но __setattr__ базового object обходит это.
    """
    import webhook as wh
    from config import settings

    original_token = settings.api_token
    object.__setattr__(settings, "api_token", VALID_TOKEN)
    wh.set_bot(mock_bot_instance)

    transport = httpx.ASGITransport(app=wh.app)
    try:
        with patch.object(wh, "_fetch_photo", new=AsyncMock(return_value=None)):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as ac:
                yield ac
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
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "bot_initialized" in data

    async def test_health_bot_initialized_true(self, client):
        resp = await client.get("/health")
        assert resp.json()["bot_initialized"] is True


# ═══════════════════════════════════════════
# POST /webhook — авторизация
# ═══════════════════════════════════════════

class TestWebhookAuth:
    async def test_no_token_returns_401(self, client):
        resp = await client.post("/webhook", json=make_payload())
        assert resp.status_code == 401

    async def test_wrong_token_returns_401(self, client):
        resp = await client.post(
            "/webhook",
            headers={"X-API-Token": "wrong-token"},
            json=make_payload(),
        )
        assert resp.status_code == 401

    async def test_valid_token_accepted(self, client):
        resp = await client.post("/webhook", headers=HEADERS, json=make_payload())
        assert resp.status_code == 200


# ═══════════════════════════════════════════
# POST /webhook — валидация payload
# ═══════════════════════════════════════════

class TestWebhookValidation:
    async def test_invalid_json_returns_422(self, client):
        resp = await client.post(
            "/webhook",
            headers={**HEADERS, "Content-Type": "application/json"},
            content=b"not valid json",
        )
        assert resp.status_code in (400, 422)

    async def test_missing_required_field_returns_422(self, client):
        resp = await client.post(
            "/webhook",
            headers=HEADERS,
            json={"user_id": 123},  # нет type и message
        )
        assert resp.status_code == 422

    async def test_valid_payload_returns_ok(self, client):
        resp = await client.post("/webhook", headers=HEADERS, json=make_payload())
        assert resp.json() == {"status": "ok"}


# ═══════════════════════════════════════════
# POST /webhook — маршрутизация уведомлений
# ═══════════════════════════════════════════

class TestWebhookRouting:
    async def test_send_to_specific_user(self, client, mock_bot_instance):
        """user_id > 0 → отправляем конкретному пользователю."""
        resp = await client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(user_id=12345),
        )
        assert resp.status_code == 200
        mock_bot_instance.send_message.assert_called_once()
        call_kwargs = mock_bot_instance.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 12345

    async def test_send_to_all_admins(self, client, mock_bot_instance):
        """user_id == -1 → отправляем всем администраторам (111, 222, 333 из conftest)."""
        resp = await client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(user_id=-1),
        )
        assert resp.status_code == 200
        assert mock_bot_instance.send_message.call_count == 3

    async def test_send_to_group(self, client, mock_bot_instance):
        """user_id == 0 → отправляем в группу (group_chat_id из env = -100123456)."""
        resp = await client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(user_id=0),
        )
        assert resp.status_code == 200
        mock_bot_instance.send_message.assert_called_once()
        call_kwargs = mock_bot_instance.send_message.call_args[1]
        assert call_kwargs["chat_id"] == -100123456

    async def test_admin_reservation_request_has_keyboard(self, client, mock_bot_instance):
        """Тип 'admin_reservation_request' → добавляются кнопки быстрого действия."""
        resp = await client.post(
            "/webhook",
            headers=HEADERS,
            json=make_payload(
                user_id=-1,
                type="admin_reservation_request",
                book_id=42,
            ),
        )
        assert resp.status_code == 200
        for call in mock_bot_instance.send_message.call_args_list:
            assert call[1].get("reply_markup") is not None

    async def test_payload_too_large_returns_413(self, client):
        """Content-Length > 1MB → 413 Payload Too Large."""
        big_content = b"x" * (1024 * 1024 + 1)
        resp = await client.post(
            "/webhook",
            headers={**HEADERS, "Content-Length": str(len(big_content))},
            content=big_content,
        )
        assert resp.status_code == 413