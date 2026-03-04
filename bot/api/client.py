"""
HTTP клиент для взаимодействия с Library API (v2)
"""
import asyncio
import logging
import mimetypes
from typing import Any, Dict, List, Optional

import aiohttp

from config import settings

logger = logging.getLogger(__name__)

_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 МБ
_IMAGE_CHUNK_SIZE = 65_536            # 64 КБ за чтение
_VALID_IMAGE_CONTENT_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif",
})


class APIError(Exception):
    """
    API-ошибка с HTTP-статусом.

    Заменяет хрупкое `"403" in str(e)` во всех хендлерах:
    теперь можно проверять `e.status == 403` напрямую.
    Гарантирует, что статус-код никогда не будет False Positive
    из-за цифр в теле ответа.
    """

    def __init__(self, status: int, message: str = ""):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}")


class APIClient:
    """Асинхронный клиент для Library API v2"""

    def __init__(self):
        self.base_url = settings.api_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._health_session: Optional[aiohttp.ClientSession] = None
        # Lock предотвращает race condition: два корутина могут одновременно
        # увидеть self._session.closed == True и создать две сессии.
        self._session_lock = asyncio.Lock()
        self._health_session_lock = asyncio.Lock()

    # =========================================
    # SESSION
    # =========================================

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if not self._session or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit=20,
                    ttl_dns_cache=300,
                    keepalive_timeout=30,
                )
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    headers={"X-API-Token": settings.api_token},
                    timeout=aiohttp.ClientTimeout(total=30, connect=5),
                )
        return self._session

    async def _get_health_session(self) -> aiohttp.ClientSession:
        """Сессия для /health без заголовка X-API-Token."""
        async with self._health_session_lock:
            if not self._health_session or self._health_session.closed:
                self._health_session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                )
        return self._health_session

    async def close(self):
        for s in (self._session, self._health_session):
            if s and not s.closed:
                await s.close()

    # =========================================
    # BASE REQUEST
    # =========================================

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """
        Выполняет HTTP-запрос с retry для 429, 500, 503 и ошибок соединения.
        Бросает APIError(status, message) для всех HTTP 4xx/5xx ответов.

        FIX: На последней попытке (attempt == 2) при 429 ранее выполнялся
        бесполезный sleep(wait), после которого `continue` выходил из цикла
        и выбрасывался RuntimeError("Превышено число попыток"). Теперь на
        последней попытке мы немедленно поднимаем APIError(429), не тратя
        время на sleep перед гарантированной ошибкой.

        Сессия получается внутри каждой попытки: если сессия закрылась
        (например, из-за ClientConnectorError на предыдущей итерации и
        последующего пересоздания), следующий retry получает свежую сессию,
        а не использует закрытую из-за закешированной ссылки снаружи loop.
        """
        url = f"{self.base_url}{endpoint}"

        for attempt in range(3):
            # Получаем сессию на каждой попытке — безопасно при закрытии.
            session = await self._get_session()
            try:
                async with session.request(method, url, **kwargs) as response:

                    if response.status == 429:
                        # FIX: не спим перед последней попыткой — это бесполезно.
                        # На attempts 0 и 1 делаем retry; на attempt 2 — сразу ошибка.
                        if attempt < 2:
                            raw = response.headers.get("Retry-After", "5")
                            try:
                                wait = min(int(raw), 60)
                            except ValueError:
                                wait = 5
                            logger.warning(
                                "Rate limited on %s (attempt %d). Retry in %ds...",
                                endpoint, attempt, wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        # attempt == 2: поднимаем явную APIError вместо RuntimeError
                        logger.error(
                            "Rate limited on %s after %d attempts, giving up.",
                            endpoint, attempt + 1,
                        )
                        raise APIError(429, "Rate limited")

                    if response.status in (500, 503) and attempt < 2:
                        wait = 2 ** attempt  # 1s, 2s
                        logger.warning(
                            "Server error %d on %s (attempt %d). Retry in %ds...",
                            response.status, endpoint, attempt, wait,
                        )
                        await asyncio.sleep(wait)
                        continue

                    if response.status == 404:
                        return None

                    try:
                        data = await response.json()
                    except Exception:
                        data = await response.text()

                    if response.status >= 400:
                        # Извлекаем читаемое сообщение из ответа API
                        detail = ""
                        if isinstance(data, dict):
                            detail = data.get("detail") or data.get("message") or str(data)
                        else:
                            detail = str(data)

                        logger.error("HTTP %d at %s: %s", response.status, endpoint, detail)
                        raise APIError(response.status, detail)

                    return data

            except asyncio.TimeoutError:
                if attempt < 2:
                    wait = 2 ** attempt
                    logger.warning(
                        "Timeout on %s (attempt %d). Retry in %ds...",
                        endpoint, attempt, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise RuntimeError(f"Превышен таймаут запроса к {endpoint}") from None

            except aiohttp.ClientConnectorError as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Не удалось подключиться к API: {e}") from e

            except APIError:
                raise  # не retry-им клиентские ошибки

        raise RuntimeError(f"Превышено число попыток для {endpoint}")

    # =========================================
    # PAGINATION HELPER
    # =========================================

    def _extract_items(self, response: Any) -> List[Dict]:
        """Извлекает список items из Page{} ответа."""
        if isinstance(response, dict) and "items" in response:
            return response["items"]
        if isinstance(response, list):
            return response
        return []

    # =========================================
    # USERS
    # =========================================

    async def auth_user(
        self,
        tg_id: int,
        full_name: str,
        username: Optional[str] = None,
    ) -> Dict:
        payload = {"tg_id": tg_id, "full_name": full_name, "username": username}
        return await self._request("POST", "/users/auth", json=payload)

    async def set_admin(self, tg_id: int, is_admin: bool, requester_id: int) -> Dict:
        payload = {"is_admin": is_admin, "requester_id": requester_id}
        return await self._request("POST", f"/users/{tg_id}/set-admin", json=payload)

    async def search_users(
        self,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if query:
            if len(query) < 2:
                return []
            params["q"] = query
        response = await self._request("GET", "/users", params=params)
        return self._extract_items(response)

    # =========================================
    # MEDIA
    # =========================================

    async def upload_media(self, file_bytes: bytes, filename: str = "image.jpg") -> Dict:
        """Определяем content_type из имени файла, не хардкодим image/jpeg."""
        content_type, _ = mimetypes.guess_type(filename)
        if not content_type or not content_type.startswith("image/"):
            content_type = "image/jpeg"

        data = aiohttp.FormData()
        data.add_field("file", file_bytes, filename=filename, content_type=content_type)
        return await self._request("POST", "/media/upload", data=data)

    async def get_image_bytes(self, image_path: str) -> Optional[bytes]:
        """
        Скачивает изображение по внутреннему пути.

        Защита:
        - Path-traversal: rejecting paths with ".." or leading "/"
        - Content-Type: принимаем только image/* ответы
        - Размер: жёсткий лимит через стриминг (Content-Length необязателен)

        Retry: до 3 попыток при TimeoutError и ClientConnectorError,
        аналогично _request — для консистентности поведения клиента.
        """
        if not image_path or ".." in image_path or image_path.startswith("/"):
            logger.warning("Rejected suspicious image_path: %r", image_path)
            return None

        url = f"{self.base_url}/media/{image_path}"

        for attempt in range(3):
            session = await self._get_session()
            try:
                async with session.get(url) as response:
                    if response.status == 404:
                        return None
                    if response.status != 200:
                        if response.status in (500, 503) and attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        logger.error("Failed to get image %s: %d", image_path, response.status)
                        return None

                    # Валидируем Content-Type — защита от получения не-изображения
                    content_type = (
                        response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    )
                    if content_type and content_type not in _VALID_IMAGE_CONTENT_TYPES:
                        logger.warning(
                            "Image %s rejected: unexpected Content-Type %r",
                            image_path, content_type,
                        )
                        return None

                    # Быстрая проверка по Content-Length до начала загрузки
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) > _IMAGE_MAX_BYTES:
                                logger.warning(
                                    "Image %s rejected: Content-Length=%s exceeds %d bytes",
                                    image_path, content_length, _IMAGE_MAX_BYTES,
                                )
                                return None
                        except ValueError:
                            pass

                    # Читаем чанками с жёстким ограничением по реальному размеру
                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in response.content.iter_chunked(_IMAGE_CHUNK_SIZE):
                        total_size += len(chunk)
                        if total_size > _IMAGE_MAX_BYTES:
                            logger.warning(
                                "Image %s aborted: exceeded %d bytes (got %d so far)",
                                image_path, _IMAGE_MAX_BYTES, total_size,
                            )
                            return None
                        chunks.append(chunk)

                    return b"".join(chunks)

            except asyncio.TimeoutError:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("Timeout fetching image %s after %d attempts", image_path, attempt + 1)
                return None
            except aiohttp.ClientConnectorError as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("Connection error fetching image %s: %s", image_path, e)
                return None
            except Exception as e:
                logger.error("Unexpected error fetching image %s: %s", image_path, e)
                return None

        return None

    # =========================================
    # BOOKS
    # =========================================

    async def get_genres(self) -> List[str]:
        result = await self._request("GET", "/books/genres")
        return result.get("genres", []) if result else []

    async def get_books(
        self,
        status: Optional[str] = None,
        genre: Optional[str] = None,
        query: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if genre:
            params["genre"] = genre[:50]
        if query:
            if len(query) < 2:
                return []
            params["query"] = query
        if user_id and user_id > 0:
            params["user_id"] = user_id
        response = await self._request("GET", "/books", params=params)
        # Явный guard на None: 404 возвращает None, _extract_items это обрабатывает,
        # но единообразная проверка здесь делает намерение прозрачным.
        return self._extract_items(response) if response is not None else []

    async def get_book(self, book_id: int) -> Optional[Dict]:
        if book_id <= 0:
            return None
        return await self._request("GET", f"/books/{book_id}")

    async def create_book(self, book_data: Dict, photo_bytes: Optional[bytes] = None) -> Dict:
        """
        Создаёт книгу через multipart/form-data.

        Фильтруем None И пустые строки: API некоторых версий интерпретирует
        пустую строку как заданное значение, что может перезаписать дефолты.
        photo_content_type / photo_filename — служебные поля, не передаются в API.
        """
        _INTERNAL_FIELDS = {"photo_content_type", "photo_filename"}

        data = aiohttp.FormData()
        for key, value in book_data.items():
            if value is not None and value != "" and key not in _INTERNAL_FIELDS:
                data.add_field(key, str(value))

        if photo_bytes:
            photo_content_type = book_data.get("photo_content_type", "image/jpeg")
            photo_filename = book_data.get("photo_filename", "cover.jpg")
            data.add_field(
                "photo", photo_bytes,
                filename=photo_filename,
                content_type=photo_content_type,
            )
        return await self._request("POST", "/books", data=data)

    async def update_book(self, book_id: int, user_id: int, update_data: Dict) -> Dict:
        # Фильтруем только None — пустые строки передаём явно (например, для удаления описания)
        payload = {
            "user_id": user_id,
            **{k: v for k, v in update_data.items() if v is not None},
        }
        return await self._request("PATCH", f"/books/{book_id}", json=payload)

    async def delete_book(self, book_id: int, user_id: int) -> Dict:
        return await self._request("DELETE", f"/books/{book_id}", params={"user_id": user_id})

    # =========================================
    # RESERVATIONS
    # =========================================

    async def request_reservation(self, book_id: int, user_id: int, days: int = 14) -> Dict:
        return await self._request(
            "POST", f"/books/{book_id}/reserve",
            json={"user_id": user_id, "days": days},
        )

    async def approve_reservation(self, book_id: int, admin_id: int, due_date: str) -> Dict:
        return await self._request(
            "POST", f"/books/{book_id}/approve",
            json={"admin_id": admin_id, "due_date": due_date},
        )

    async def reject_reservation(self, book_id: int, admin_id: int, reason: str) -> Dict:
        return await self._request(
            "POST", f"/books/{book_id}/reject",
            json={"admin_id": admin_id, "reason": reason},
        )

    async def return_book(
        self,
        book_id: int,
        user_id: int,
        photo_bytes: Optional[bytes] = None,
        photo_filename: str = "return_photo.jpg",
    ) -> Dict:
        data = aiohttp.FormData()
        data.add_field("user_id", str(user_id))
        if photo_bytes:
            content_type, _ = mimetypes.guess_type(photo_filename)
            if not content_type or not content_type.startswith("image/"):
                content_type = "image/jpeg"
            data.add_field(
                "photo", photo_bytes,
                filename=photo_filename,
                content_type=content_type,
            )
        return await self._request("POST", f"/books/{book_id}/return", data=data)

    # =========================================
    # WAITLIST
    # =========================================

    async def join_waitlist(self, book_id: int, user_id: int) -> Dict:
        return await self._request(
            "POST", f"/books/{book_id}/waitlist",
            json={"user_id": user_id},
        )

    async def leave_waitlist(self, book_id: int, user_id: int) -> Dict:
        return await self._request(
            "DELETE", f"/books/{book_id}/waitlist",
            params={"user_id": user_id},
        )

    # =========================================
    # BOOK HISTORY
    # =========================================

    async def get_book_history(self, book_id: int, limit: int = 50, offset: int = 0) -> List[Dict]:
        params = {"limit": limit, "offset": offset}
        response = await self._request("GET", f"/books/{book_id}/history", params=params)
        return self._extract_items(response) if response else []

    # =========================================
    # ADMIN
    # =========================================

    async def get_pending_reservations(
        self, requester_id: int, limit: int = 50, offset: int = 0
    ) -> List[Dict]:
        params = {"limit": limit, "offset": offset}
        response = await self._request(
            "POST", "/admin/pending-reservations",
            params=params,
            json={"requester_id": requester_id},
        )
        return self._extract_items(response) if response else []

    # =========================================
    # HEALTH
    # =========================================

    async def health_check(self) -> Dict:
        """Единственный эндпоинт без X-API-Token. Использует отдельную сессию."""
        session = await self._get_health_session()
        try:
            async with session.get(f"{self.base_url}/health") as response:
                return await response.json()
        except asyncio.TimeoutError:
            logger.error("Health check timeout")
            return {"status": "timeout"}
        except Exception as e:
            logger.error("Health check error: %s", e)
            return {"status": "unknown"}


# Singleton
api = APIClient()