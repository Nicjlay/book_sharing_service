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

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    "X-API-Token": settings.api_token
                }
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        async with session.request(method, url, **kwargs) as response:

            if response.status == 404:
                return None

            if response.status == 422:
                error_json = await response.json()
                print(f"\n❌ 422 Validation Error at {endpoint}")
                print(error_json)
                return None

            if response.status >= 400:
                text = await response.text()
                print(f"\n❌ HTTP {response.status} Error at {endpoint}")
                print(text)
                response.raise_for_status()

            if response.content_type == "application/json":
                return await response.json()

            return await response.text()

    # =========================
    # USERS
    # =========================

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

    async def search_users(self, query: str = None) -> List[Dict]:
        params = {"q": query} if query else None
        return await self._request("GET", "/users", params=params)

    # =========================
    # BOOKS
    # =========================

    async def get_genres(self) -> List[str]:
        result = await self._request("GET", "/books/genres")
        return result.get("genres", []) if result else []

    async def get_books(
        self,
        status: str = None,
        genre: str = None,
        query: str = None,
        user_id: int = None
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

        return await self._request("GET", "/books", params=params or None)

    async def get_book(self, book_id: int) -> Optional[Dict]:
        return await self._request("GET", f"/books/{book_id}")

    async def create_book(self, book_data: Dict) -> Dict:
        return await self._request("POST", "/books", json=book_data)

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

    # =========================
    # RESERVATIONS
    # =========================

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


# Singleton
api = APIClient()
