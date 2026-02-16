"""
HTTP клиент для взаимодействия с Library API
"""

import aiohttp
from typing import Optional, List, Dict, Any
from config import settings


class APIClient:
    """Асинхронный клиент для Library API"""

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
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        async with session.request(method, url, **kwargs) as response:

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
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=str(data),
                    headers=response.headers,
                )

            return data

    # =========================================
    # USERS
    # =========================================

    async def auth_user(
        self,
        tg_id: int,
        full_name: str,
        username: Optional[str] = None,
        is_admin: bool = False
    ) -> Dict:

        payload = {
            "tg_id": tg_id,
            "full_name": full_name,
            "username": username,
            "is_admin": is_admin
        }

        return await self._request(
            "POST",
            "/users/auth",
            json=payload
        )

    async def search_users(self, query: Optional[str] = None) -> List[Dict]:
        params = {"q": query} if query else None
        return await self._request("GET", "/users", params=params)

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

        return await self._request(
            "POST",
            "/media/upload",
            data=data
        )

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
        user_id: Optional[int] = None
    ) -> List[Dict]:

        params = {}
        if status:
            params["status"] = status
        if genre:
            params["genre"] = genre
        if query:
            params["query"] = query
        if user_id:
            params["user_id"] = user_id

        return await self._request(
            "GET",
            "/books",
            params=params or None
        )

    async def get_book(self, book_id: int) -> Optional[Dict]:
        return await self._request("GET", f"/books/{book_id}")

    async def create_book(self, book_data: Dict, photo_bytes: Optional[bytes] = None) -> Dict:
        data = aiohttp.FormData()

        for key, value in book_data.items():
            if value is not None:
                data.add_field(key, str(value))

        if photo_bytes:
            data.add_field(
                "photo",
                photo_bytes,
                filename="cover.jpg",
                content_type="image/jpeg"
            )

        return await self._request(
            "POST",
            "/books",
            data=data
        )

    async def update_book(self, book_id: int, user_id: int, update_data: Dict) -> Dict:
        return await self._request(
            "PATCH",
            f"/books/{book_id}",
            params={"user_id": user_id},
            json=update_data
        )

    async def delete_book(self, book_id: int, user_id: int) -> Dict:
        return await self._request(
            "DELETE",
            f"/books/{book_id}",
            params={"user_id": user_id}
        )

    # =========================================
    # RESERVATIONS
    # =========================================

    async def request_reservation(self, book_id: int, user_id: int, days: int = 14) -> Dict:
        return await self._request(
            "POST",
            "/borrowings/request",
            params={"book_id": book_id},
            json={"user_id": user_id, "days": days}
        )

    async def approve_reservation(self, book_id: int, admin_id: int, due_date: str) -> Dict:
        return await self._request(
            "POST",
            "/borrowings/approve",
            params={"book_id": book_id},
            json={"admin_id": admin_id, "due_date": due_date}
        )

    async def reject_reservation(self, book_id: int, admin_id: int, reason: str) -> Dict:
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
        photo_bytes: Optional[bytes] = None
    ) -> Dict:

        data = aiohttp.FormData()
        data.add_field("user_id", str(user_id))
        data.add_field("is_admin", str(is_admin).lower())

        if photo_bytes:
            data.add_field(
                "photo",
                photo_bytes,
                filename="return_photo.jpg",
                content_type="image/jpeg"
            )

        return await self._request(
            "POST",
            "/borrowings/return",
            params={"book_id": book_id},
            data=data
        )

    async def get_image_bytes(self, image_path: str) -> Optional[bytes]:
        """
        Скачивает изображение из API по внутреннему пути (напр. 'books/uuid.webp')
        """
        session = await self._get_session()
        # Используем внутреннее имя сервиса из docker-compose
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

# Singleton
api = APIClient()
