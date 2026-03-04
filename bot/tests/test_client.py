"""
test_client.py — тесты для api/client.py (HTTP клиент)

APIClient делает реальные HTTP-запросы к бэкенду.
В тестах мы НИКОГДА не делаем настоящих запросов.
Вместо этого — мокаем aiohttp.ClientSession.

Техника: используем unittest.mock.patch как контекстный менеджер.
patch('api.client.aiohttp.ClientSession') заменяет класс сессии на наш мок.

Запуск:
    pytest tests/test_client.py -v
"""
import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from api.client import APIClient, APIError


# ─────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────

@pytest.fixture
def api_client():
    """Свежий экземпляр клиента для каждого теста."""
    return APIClient()


def make_mock_response(status: int, json_data=None, text_data: str = ""):
    """
    Создаёт мок HTTP-ответа от aiohttp.
    aiohttp использует асинхронный контекстный менеджер (async with session.get(...) as resp),
    поэтому мок должен поддерживать __aenter__ / __aexit__.
    """
    response = MagicMock()
    response.status = status
    response.headers = {}

    if json_data is not None:
        response.json = AsyncMock(return_value=json_data)
    else:
        response.json = AsyncMock(side_effect=Exception("not json"))
        response.text = AsyncMock(return_value=text_data)

    # Поддержка async with
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def make_mock_session(response):
    """Мок сессии aiohttp, возвращающий заданный ответ."""
    session = MagicMock()
    session.closed = False
    session.request = MagicMock(return_value=response)
    session.close = AsyncMock()
    return session


# ─────────────────────────────────────────────
# APIError
# ─────────────────────────────────────────────

class TestAPIError:
    def test_has_status(self):
        err = APIError(404, "Not found")
        assert err.status == 404

    def test_has_message(self):
        err = APIError(403, "Forbidden")
        assert err.message == "Forbidden"

    def test_str_representation(self):
        err = APIError(500, "Server error")
        assert "500" in str(err)
        assert "Server error" in str(err)

    def test_is_exception(self):
        err = APIError(400, "Bad request")
        assert isinstance(err, Exception)


# ─────────────────────────────────────────────
# _extract_items
# ─────────────────────────────────────────────

class TestExtractItems:
    def test_extracts_from_page_dict(self, api_client):
        data = {"items": [{"id": 1}, {"id": 2}], "total": 2}
        assert api_client._extract_items(data) == [{"id": 1}, {"id": 2}]

    def test_returns_list_as_is(self, api_client):
        data = [{"id": 1}, {"id": 2}]
        assert api_client._extract_items(data) == data

    def test_returns_empty_for_unknown_format(self, api_client):
        assert api_client._extract_items({"other": "data"}) == []

    def test_returns_empty_for_none(self, api_client):
        assert api_client._extract_items(None) == []

    def test_returns_empty_for_string(self, api_client):
        assert api_client._extract_items("string") == []


# ─────────────────────────────────────────────
# get_book — входная валидация
# ─────────────────────────────────────────────

class TestGetBook:
    @pytest.mark.asyncio
    async def test_returns_none_for_zero_id(self, api_client):
        result = await api_client.get_book(0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_negative_id(self, api_client):
        result = await api_client.get_book(-5)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_book_for_valid_id(self, api_client):
        book_data = {"id": 42, "title": "Тест", "author": "Авт Ор", "status": "available"}
        response = make_mock_response(200, json_data=book_data)
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        result = await api_client.get_book(42)
        assert result["id"] == 42

    @pytest.mark.asyncio
    async def test_returns_none_for_404(self, api_client):
        response = make_mock_response(404)
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        result = await api_client.get_book(999)
        assert result is None


# ─────────────────────────────────────────────
# get_books — входная валидация
# ─────────────────────────────────────────────

class TestGetBooks:
    @pytest.mark.asyncio
    async def test_short_query_returns_empty_without_request(self, api_client):
        """Запрос короче 2 символов — клиент не должен делать HTTP-запрос."""
        with patch.object(api_client, '_request', new=AsyncMock()) as mock_req:
            result = await api_client.get_books(query="a")
        assert result == []
        mock_req.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_query_makes_request(self, api_client):
        """Пустой query — запрос выполняется без фильтра."""
        with patch.object(api_client, '_request', new=AsyncMock(return_value=[])) as mock_req:
            result = await api_client.get_books(query=None)
        mock_req.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_user_id_ignored(self, api_client):
        """user_id <= 0 не должен добавляться в параметры запроса."""
        with patch.object(api_client, '_request', new=AsyncMock(return_value=[])) as mock_req:
            await api_client.get_books(user_id=0)
        call_kwargs = mock_req.call_args[1]  # kwargs вызова
        params = call_kwargs.get("params", {})
        assert "user_id" not in params

    @pytest.mark.asyncio
    async def test_none_response_returns_empty_list(self, api_client):
        with patch.object(api_client, '_request', new=AsyncMock(return_value=None)):
            result = await api_client.get_books()
        assert result == []


# ─────────────────────────────────────────────
# get_image_bytes — безопасность путей
# ─────────────────────────────────────────────

class TestGetImageBytes:
    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, api_client):
        result = await api_client.get_image_bytes("../../../etc/passwd")
        assert result is None

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, api_client):
        result = await api_client.get_image_bytes("/etc/shadow")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_path_rejected(self, api_client):
        result = await api_client.get_image_bytes("")
        assert result is None


# ─────────────────────────────────────────────
# _request — retry и обработка ошибок
# ─────────────────────────────────────────────

class TestRequestMethod:
    @pytest.mark.asyncio
    async def test_404_returns_none(self, api_client):
        response = make_mock_response(404)
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        result = await api_client._request("GET", "/notfound")
        assert result is None

    @pytest.mark.asyncio
    async def test_400_raises_api_error(self, api_client):
        response = make_mock_response(400, json_data={"detail": "Bad request"})
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        with pytest.raises(APIError) as exc_info:
            await api_client._request("GET", "/bad")
        assert exc_info.value.status == 400

    @pytest.mark.asyncio
    async def test_403_raises_api_error_with_status(self, api_client):
        response = make_mock_response(403, json_data={"detail": "Forbidden"})
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        with pytest.raises(APIError) as exc_info:
            await api_client._request("POST", "/admin")
        assert exc_info.value.status == 403

    @pytest.mark.asyncio
    async def test_200_returns_data(self, api_client):
        data = {"id": 1, "name": "Test"}
        response = make_mock_response(200, json_data=data)
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        result = await api_client._request("GET", "/test")
        assert result == data

    @pytest.mark.asyncio
    async def test_429_raises_after_retries(self, api_client):
        """
        429 Rate Limited: на последней попытке должен бросить APIError(429),
        а не RuntimeError.
        """
        response = make_mock_response(429)
        response.headers = {"Retry-After": "1"}
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        with patch('asyncio.sleep', new=AsyncMock()):
            with pytest.raises(APIError) as exc_info:
                await api_client._request("GET", "/limited")
        assert exc_info.value.status == 429

    @pytest.mark.asyncio
    async def test_error_detail_from_dict(self, api_client):
        """Детали ошибки извлекаются из поля 'detail' ответа."""
        response = make_mock_response(422, json_data={"detail": "Validation failed"})
        session = make_mock_session(response)
        api_client._session = session
        api_client._session_lock = asyncio.Lock()

        with pytest.raises(APIError) as exc_info:
            await api_client._request("POST", "/validate")
        assert "Validation failed" in exc_info.value.message


# ─────────────────────────────────────────────
# search_users — входная валидация
# ─────────────────────────────────────────────

class TestSearchUsers:
    @pytest.mark.asyncio
    async def test_short_query_returns_empty(self, api_client):
        with patch.object(api_client, '_request', new=AsyncMock()) as mock_req:
            result = await api_client.search_users(query="a")
        assert result == []
        mock_req.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_query_makes_request(self, api_client):
        with patch.object(api_client, '_request', new=AsyncMock(return_value=[])):
            result = await api_client.search_users(query=None)
        assert result == []


# ─────────────────────────────────────────────
# get_genres
# ─────────────────────────────────────────────

class TestGetGenres:
    @pytest.mark.asyncio
    async def test_extracts_genres_list(self, api_client):
        with patch.object(api_client, '_request', new=AsyncMock(return_value={"genres": ["Роман", "Фантастика"]})):
            result = await api_client.get_genres()
        assert result == ["Роман", "Фантастика"]

    @pytest.mark.asyncio
    async def test_none_response_returns_empty(self, api_client):
        with patch.object(api_client, '_request', new=AsyncMock(return_value=None)):
            result = await api_client.get_genres()
        assert result == []


# ─────────────────────────────────────────────
# close
# ─────────────────────────────────────────────

class TestClose:
    @pytest.mark.asyncio
    async def test_close_without_sessions(self, api_client):
        """close() не должен падать если сессии ещё не созданы."""
        await api_client.close()  # не должно выбрасывать исключение

    @pytest.mark.asyncio
    async def test_close_closes_open_sessions(self, api_client):
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        api_client._session = mock_session

        await api_client.close()
        mock_session.close.assert_called_once()
