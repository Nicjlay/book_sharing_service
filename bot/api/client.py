"""
HTTP клиент для взаимодействия с Library API
"""
import aiohttp
from typing import Optional, List, Dict, Any
from config import settings


class APIClient:
    """Асинхронный клиент для Library API"""
    
    def __init__(self):
        self.base_url = settings.api_url
        self.headers = {
            "X-API-Token": settings.api_token,
            "Content-Type": "application/json"
        }

    async def _request(self, method: str, endpoint: str, **kwargs) -> str | None | Any:
        """Базовый метод для HTTP запросов с автоматической очисткой типов"""
        url = f"{self.base_url}{endpoint}"

        # --- Очистка params от bool (нужна только для GET/query параметров) ---
        if "params" in kwargs and kwargs["params"]:
            kwargs["params"] = {
                k: (int(v) if isinstance(v, bool) else v)
                for k, v in kwargs["params"].items()
                if v is not None
            }

        if "headers" in kwargs:
            kwargs["headers"].update(self.headers)
        else:
            kwargs["headers"] = self.headers

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, **kwargs) as response:
                if response.status == 404:
                    return None

                # Если 422 - логируем тело ответа, чтобы видеть ЧТО именно не так
                if response.status == 422:
                    error_text = await response.text()
                    print(f"❌ API 422 Error at {endpoint}: {error_text}")
                    # Дадим коду упасть с raise_for_status ниже или вернем None

                response.raise_for_status()

                if response.content_type == "application/json":
                    return await response.json()
                return await response.text()

    # --- USERS ---

    async def auth_user(self, tg_id: int, full_name: str, username: str = None, is_admin: bool = False):
        """Авторизация/регистрация пользователя"""
        # --- ИСПРАВЛЕНИЕ: Отправляем чистые типы ---
        # Для JSON тела не нужно конвертировать bool в int, Python/Pydantic поймут.
        # Кириллица в full_name обрабатывается автоматически aiohttp (UTF-8).
        payload = {
            "tg_id": tg_id,
            "full_name": full_name,
            "username": username,
            "is_admin": is_admin  # Отправляем как bool (true/false)
        }

        return await self._request(
            "POST",
            "/users/auth",
            json=payload
        )

    async def search_users(self, query: str = None) -> List[Dict]:
        """Поиск пользователей (для Шага 5 визарда)"""
        params = {"q": query} if query else {}
        return await self._request("GET", "/users", params=params)

    # --- BOOKS ---

    async def get_genres(self) -> List[str]:
        """Получить список жанров"""
        result = await self._request("GET", "/books/genres")
        return result.get("genres", []) if result else []

    async def get_books(
        self,
        status: str = None,
        genre: str = None,
        query: str = None,
        user_id: int = None
    ) -> List[Dict]:
        """Получить книги с фильтрами"""
        params = {}
        if status:
            params["status"] = status
        if genre:
            params["genre"] = genre
        if query:
            params["query"] = query
        if user_id:
            params["user_id"] = user_id

        return await self._request("GET", "/books", params=params)

    async def get_book(self, book_id: int) -> Optional[Dict]:
        """Получить детали книги"""
        return await self._request("GET", f"/books/{book_id}")

    async def get_book_history(self, book_id: int) -> List[Dict]:
        """Получить историю книги"""
        return await self._request("GET", f"/books/{book_id}/history")

    async def create_book(self, book_data: Dict) -> Dict:
        """Создать книгу через визард"""
        return await self._request("POST", "/books", json=book_data)

    async def update_book(self, book_id: int, user_id: int, update_data: Dict) -> Dict:
        """Редактировать книгу"""
        return await self._request(
            "PATCH",
            f"/books/{book_id}",
            params={"user_id": user_id},
            json=update_data
        )

    async def delete_book(self, book_id: int, user_id: int) -> Dict:
        """Удалить книгу"""
        return await self._request(
            "DELETE",
            f"/books/{book_id}",
            params={"user_id": user_id}
        )

    async def upload_media(self, file_data: bytes, filename: str) -> str:
        """Загрузить изображение"""
        data = aiohttp.FormData()
        data.add_field('file', file_data, filename=filename, content_type='image/jpeg')

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/media/upload",
                data=data,
                headers={"X-API-Token": settings.api_token}
            ) as response:
                response.raise_for_status()
                result = await response.json()
                return result.get("path")

    # --- RESERVATIONS ---

    async def request_reservation(self, book_id: int, user_id: int, days: int = 14) -> Dict:
        """Запросить бронирование книги"""
        return await self._request(
            "POST",
            "/borrowings/request",
            params={"book_id": book_id},
            json={"user_id": user_id, "days": days}
        )

    async def approve_reservation(self, book_id: int, admin_id: int, due_date: str) -> Dict:
        """Админ подтверждает выдачу"""
        return await self._request(
            "POST",
            "/borrowings/approve",
            params={"book_id": book_id},
            json={"admin_id": admin_id, "due_date": due_date}
        )

    async def reject_reservation(self, book_id: int, admin_id: int, reason: str) -> Dict:
        """Админ отклоняет бронь"""
        return await self._request(
            "POST",
            "/borrowings/reject",
            params={"book_id": book_id},
            json={"admin_id": admin_id, "reason": reason}
        )

    async def return_book(
            self,
            book_id: int,
            user_id: int,
            is_admin: bool = False,
            photo_data: bytes = None
    ) -> Dict:
        """Вернуть книгу"""

        data = aiohttp.FormData()
        data.add_field('user_id', str(user_id))
        data.add_field('is_admin', str(int(is_admin)))

        if photo_data:
            data.add_field('photo', photo_data, filename='return_photo.jpg', content_type='image/jpeg')

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    f"{self.base_url}/borrowings/return",
                    params={"book_id": book_id},
                    data=data,
                    headers={"X-API-Token": settings.api_token}
            ) as response:
                response.raise_for_status()
                return await response.json()

    async def join_waitlist(self, book_id: int, user_id: int) -> Dict:
        """Добавиться в лист ожидания"""
        return await self._request(
            "POST",
            f"/books/{book_id}/waitlist",
            params={"user_id": user_id}
        )

    # --- ADMIN ---

    async def get_pending_reservations(self) -> List[Dict]:
        """Получить список pending reservations для админа"""
        return await self._request("GET", "/admin/pending-reservations")


# Singleton instance
api = APIClient()