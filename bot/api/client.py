"""
HTTP клиент для взаимодействия с Library API (v2)
"""

import asyncio
import aiohttp
from typing import Optional, List, Dict, Any
from config import settings


class APIClient:
    """Асинхронный клиент для Library API v2"""

    def __init__(self):
        self.base_url = settings.api_url.rstrip("/")
        self.session: Optional[aiohttp.ClientSession] = None

    # =========================================
    # SESSION
    # =========================================

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    "X-API-Token": settings.api_token
                },
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # =========================================
    # BASE REQUEST
    # =========================================

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """
        Выполняет HTTP-запрос с обработкой новых кодов ответа v2:
          - 429: Retry-After — ждём и повторяем
          - 413: тело запроса слишком большое
          - 415: неподдерживаемый тип файла
          - 404: возвращаем None
          - 503: сервис временно недоступен
        """
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        for attempt in range(3):
            async with session.request(method, url, **kwargs) as response:

                # 429 — превышен лимит запросов, ждём Retry-After
                if response.status == 429:
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    print(f"⚠️ Rate limited. Retrying after {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue

                # 404 — просто возвращаем None
                if response.status == 404:
                    return None

                # Попытка прочитать JSON
                try:
                    data = await response.json()
                except Exception:
                    data = await response.text()

                # Обработка ошибок
                if response.status >= 400:
                    print(f"\n❌ HTTP {response.status} at {endpoint}")
                    print(data)

                    if response.status == 413:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=413,
                            message="Request body too large (>1MB)",
                            headers=response.headers,
                        )
                    if response.status == 415:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=415,
                            message="Unsupported media type. Allowed: JPEG, PNG, WebP, GIF",
                            headers=response.headers,
                        )

                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=str(data),
                        headers=response.headers,
                    )

                return data

        # Если все попытки после 429 исчерпаны
        raise RuntimeError(f"Max retries exceeded for {endpoint}")

    # =========================================
    # PAGINATION HELPER
    # =========================================

    def _extract_items(self, response: Any) -> List[Dict]:
        """
        Извлекает список items из нового формата Page{} ответа.
        Все list-эндпоинты теперь возвращают:
          { "items": [...], "total": N, "limit": 50, "offset": 0 }
        При поиске (query) total == -1; используем len(items) == limit
        для определения наличия следующей страницы.
        """
        if isinstance(response, dict) and "items" in response:
            return response["items"]
        # Фолбек: если вдруг вернулся прямой список (не должно быть в v2)
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
        """
        POST /users/auth
        ИЗМЕНЕНИЕ v2: поле is_admin удалено из запроса.
        Управление правами через отдельный эндпоинт set_admin.
        """
        payload = {
            "tg_id": tg_id,
            "full_name": full_name,
            "username": username,
        }
        return await self._request("POST", "/users/auth", json=payload)

    async def set_admin(
        self,
        tg_id: int,
        is_admin: bool,
        requester_id: int,
    ) -> Dict:
        """
        НОВЫЙ v2: POST /users/{tg_id}/set-admin
        Управление флагом администратора. Требует прав у вызывающего.
        Администратор не может лишить себя прав.
        """
        payload = {
            "is_admin": is_admin,
            "requester_id": requester_id,
        }
        return await self._request("POST", f"/users/{tg_id}/set-admin", json=payload)

    async def search_users(
        self,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        """
        GET /users
        ИЗМЕНЕНИЕ v2: возвращает Page[UserRead] вместо List[UserRead].
        Параметр q: минимум 2 символа, пустая строка = показать всех.
        UserRead теперь включает поля created_at и tg_id (алиас для id).
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        # Пустая строка = показать всех; строка < 2 символов → API вернёт 400
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
        data = aiohttp.FormData()
        data.add_field(
            "file",
            file_bytes,
            filename=filename,
            content_type="image/jpeg"
        )
        return await self._request("POST", "/media/upload", data=data)

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
        """
        GET /books → Page[BookRead]
        ИЗМЕНЕНИЕ v2:
          - query: минимум 2 символа, иначе HTTP 400
          - genre: max_length 50 символов
          - user_id: должен быть > 0
          - добавлены limit/offset (пагинация)
          - genre в ответе может быть null (не конвертируется в "Другое")
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if genre:
            params["genre"] = genre[:50]  # max_length = 50
        if query:
            if len(query) < 2:
                return []
            params["query"] = query
        if user_id and user_id > 0:
            params["user_id"] = user_id

        response = await self._request("GET", "/books", params=params or None)
        return self._extract_items(response)

    async def get_book(self, book_id: int) -> Optional[Dict]:
        """
        GET /books/{book_id}
        ИЗМЕНЕНИЕ v2: book_id должен быть > 0.
        Сообщение ошибки на русском: "Книга не найдена".
        Поле genre может быть null.
        """
        if book_id <= 0:
            return None
        return await self._request("GET", f"/books/{book_id}")

    async def create_book(self, book_data: Dict, photo_bytes: Optional[bytes] = None) -> Dict:
        """
        POST /books
        ИЗМЕНЕНИЕ v2: добавлено поле isbn (опционально, max 20 символов).
        author: min_length=2 (было 3), убрана проверка на "2 слова".
        title: min_length=1, max_length=200, whitespace trim.
        owner_id: должен быть > 0.
        description: max_length=2000.
        genre: max_length=50.
        """
        data = aiohttp.FormData()
        for key, value in book_data.items():
            if value is not None:
                data.add_field(key, str(value))

        if photo_bytes:
            data.add_field(
                "photo",
                photo_bytes,
                filename="base_cover.jpg",
                content_type="image/jpeg"
            )

        return await self._request("POST", "/books", data=data)

    async def update_book(self, book_id: int, user_id: int, update_data: Dict) -> Dict:
        """
        PATCH /books/{book_id}
        КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ v2: user_id перенесён из query-параметра в JSON-тело.
        Было: PATCH /books/1?user_id=123  { "title": "..." }
        Стало: PATCH /books/1  { "user_id": 123, "title": "..." }
        Поле isbn теперь также поддерживается.
        """
        payload = {"user_id": user_id, **update_data}
        return await self._request("PATCH", f"/books/{book_id}", json=payload)

    async def delete_book(self, book_id: int, user_id: int) -> Dict:
        """
        DELETE /books/{book_id}
        ИЗМЕНЕНИЕ v2: user_id > 0 обязательно.
        Ответ изменился с произвольного JSON на {"status": "deleted"}.
        """
        return await self._request(
            "DELETE",
            f"/books/{book_id}",
            params={"user_id": user_id}
        )

    # =========================================
    # RESERVATIONS
    # =========================================

    async def request_reservation(self, book_id: int, user_id: int, days: int = 14) -> Dict:
        """
        POST /books/{book_id}/reserve
        ИЗМЕНЕНИЕ v2: days теперь ge=1, le=90 (было только default=14).
        user_id: gt=0.
        Новая ошибка 409 при превышении лимита одновременных книг (>5).
        """
        return await self._request(
            "POST",
            f"/books/{book_id}/reserve",
            json={"user_id": user_id, "days": days}
        )

    async def approve_reservation(self, book_id: int, admin_id: int, due_date: str) -> Dict:
        """
        POST /books/{book_id}/approve
        ИЗМЕНЕНИЕ v2: due_date обязана быть в будущем и не далее чем через 730 дней.
        admin_id: gt=0. HTTP 403 если не администратор.
        """
        return await self._request(
            "POST",
            f"/books/{book_id}/approve",
            json={"admin_id": admin_id, "due_date": due_date}
        )

    async def reject_reservation(self, book_id: int, admin_id: int, reason: str) -> Dict:
        """
        POST /books/{book_id}/reject
        ИЗМЕНЕНИЕ v2: reason max_length=500. admin_id: gt=0.
        HTTP 400 если книга не в статусе RESERVED.
        """
        return await self._request(
            "POST",
            f"/books/{book_id}/reject",
            json={"admin_id": admin_id, "reason": reason}
        )

    async def return_book(
        self,
        book_id: int,
        user_id: int,
        photo_bytes: Optional[bytes] = None,
    ) -> Dict:
        """
        POST /books/{book_id}/return
        КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ v2: поле is_admin удалено из FormData.
        Права определяются сервером по admin-флагу пользователя.
        Ответ изменился на {"status": "returned"}.
        """
        data = aiohttp.FormData()
        data.add_field("user_id", str(user_id))
        # is_admin УДАЛЁН — сервер определяет права самостоятельно

        if photo_bytes:
            data.add_field(
                "photo",
                photo_bytes,
                filename="return_photo.jpg",
                content_type="image/jpeg"
            )

        return await self._request("POST", f"/books/{book_id}/return", data=data)

    # =========================================
    # WAITLIST
    # =========================================

    async def join_waitlist(self, book_id: int, user_id: int) -> Dict:
        """
        POST /books/{book_id}/waitlist
        КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ v2: user_id теперь в JSON-теле, не query-параметр.
        Было: POST /books/1/waitlist?user_id=123
        Стало: POST /books/1/waitlist  { "user_id": 123 }
        Новый формат ответа: {"message": "...", "added": true/false}
        added=false если уже в очереди (не ошибка).
        HTTP 400 если пытается встать в очередь на свою или держимую книгу.
        """
        return await self._request(
            "POST",
            f"/books/{book_id}/waitlist",
            json={"user_id": user_id}
        )

    async def leave_waitlist(self, book_id: int, user_id: int) -> Dict:
        """
        НОВЫЙ v2: DELETE /books/{book_id}/waitlist?user_id={user_id}
        Идемпотентный: если пользователь не в очереди — всё равно HTTP 200.
        """
        return await self._request(
            "DELETE",
            f"/books/{book_id}/waitlist",
            params={"user_id": user_id}
        )

    # =========================================
    # BOOK HISTORY
    # =========================================

    async def get_book_history(
        self,
        book_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        """
        GET /books/{book_id}/history → Page[BookHistoryRead]
        ИЗМЕНЕНИЕ v2: возвращает Page вместо List.
        BookStatus теперь включает DELETED ("deleted") для мягко удалённых книг.
        """
        params = {"limit": limit, "offset": offset}
        response = await self._request(
            "GET",
            f"/books/{book_id}/history",
            params=params
        )
        return self._extract_items(response) if response else []

    # =========================================
    # ADMIN
    # =========================================

    async def get_pending_reservations(
        self,
        requester_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        """
        КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ v2:
        Было:    GET  /admin/pending-reservations
        Стало:   POST /admin/pending-reservations  { "requester_id": ... }
        Добавлена авторизационная проверка: HTTP 403 если не администратор.
        Ответ: Page[BookRead] с пагинацией (limit/offset в query).
        """
        params = {"limit": limit, "offset": offset}
        response = await self._request(
            "POST",
            "/admin/pending-reservations",
            params=params,
            json={"requester_id": requester_id}
        )
        return self._extract_items(response) if response else []

    # =========================================
    # MEDIA
    # =========================================

    async def get_image_bytes(self, image_path: str) -> Optional[bytes]:
        """
        Скачивает изображение из API по внутреннему пути (напр. 'books/uuid.webp').
        image_path возвращается как есть из БД (без regex-валидации в v2).
        """
        session = await self._get_session()
        url = f"{self.base_url}/media/{image_path}"
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                print(f"❌ Failed to get image: {response.status}")
                return None
        except Exception as e:
            print(f"❌ Connection error to API: {e}")
            return None

    # =========================================
    # HEALTH
    # =========================================

    async def health_check(self) -> Dict:
        """
        GET /health — единственный эндпоинт БЕЗ X-API-Token.
        Теперь проверяет доступность БД.
        HTTP 503 при недоступности: {"status": "unhealthy", "detail": "Database unavailable"}
        HTTP 200 при норме:         {"status": "ok", "service": "Library API"}
        """
        # Делаем запрос без токена — временно создаём сессию без заголовка
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            url = f"{self.base_url}/health"
            try:
                async with session.get(url) as response:
                    data = await response.json()
                    return data
            except Exception as e:
                print(f"❌ Health check error: {e}")
                return {"status": "unknown"}


# Singleton
api = APIClient()
