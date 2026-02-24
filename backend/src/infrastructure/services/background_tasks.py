"""
Background tasks и Push-уведомления через HTTP webhook (Push Architecture)
"""
import asyncio
import httpx
import os
from datetime import datetime, timedelta
# Fix #4: Optional отсутствовал в imports — TypeError при создании NotificationService
from typing import Any, Dict, List, Optional

from domain.domain_models import BookStatus, NotificationType
# Fix #3: был неверный путь infrastructure.db.tables — такого модуля не существует
from infrastructure.db.models import BookTable

BOT_WEBHOOK_URL = os.getenv("BOT_WEBHOOK_URL", "http://library_bot:8001/webhook")
API_TOKEN = os.getenv("API_TOKEN", "")


class NotificationService:
    """Сервис push-уведомлений: отправляет HTTP POST на бот"""

    def __init__(self):
        # Единственный httpx клиент на весь процесс — создаётся один SSL context
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Ленивая инициализация — переиспользуем один клиент."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=5.0)
        return self._http_client

    async def close(self):
        """Закрыть клиент при shutdown (вызывается в lifespan)."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _send_http_notification(self, payload: Dict[str, Any]):
        """Отправка уведомления в бот через HTTP"""
        if hasattr(payload.get("type"), "value"):
            payload["type"] = payload["type"].value

        print(f"📡 PUSH to Bot {payload.get('user_id')}: {payload.get('type')}")

        try:
            client = await self._get_http_client()
            await client.post(
                BOT_WEBHOOK_URL,
                json=payload,
                headers={"X-API-Token": API_TOKEN}
            )
        except Exception as e:
            print(f"❌ Bot is not reachable at {BOT_WEBHOOK_URL}: {e}")

    # ------------------------------------------------------------------
    # УВЕДОМЛЕНИЯ
    # ------------------------------------------------------------------

    async def notify_new_book(self, book: Any, owner_username: str):
        """Уведомление о новой книге всем пользователям (через group chat)"""
        msg = f"📖 Новая книга в библиотеке!\n\n<b>{book.title}</b>\n✍️ {book.author}\n👤 @{owner_username}"
        payload = {
            "user_id": 0,
            "type": NotificationType.NEW_BOOK.value,
            "message": msg,
            "book_id": book.id,
            "meta": {"owner_username": owner_username}
        }
        await self._send_http_notification(payload)

    async def notify_owner_about_return(self, book: BookTable, returner_id: int,
                                        photo_path: str = None,
                                        returner_username: str = None):
        """Уведомление владельцу о возврате книги (ТЗ 3.3.3)"""
        if returner_username:
            reader_mention = f"<a href=\"tg://user?id={returner_id}\">@{returner_username}</a>"
        else:
            reader_mention = f"<a href=\"tg://user?id={returner_id}\">Читатель</a>"

        msg = f"📚 Ваша книга <b>«{book.title}»</b> возвращена!\n\nВернул: {reader_mention}"
        if photo_path:
            msg += "\n📸 Приложено фото состояния книги."

        payload = {
            "user_id": book.owner_id,
            "type": NotificationType.BOOK_RETURNED.value,
            "message": msg,
            "book_id": book.id,
            "meta": {
                "photo_path": photo_path,
                "returner_id": returner_id,
                "returner_username": returner_username
            }
        }
        await self._send_http_notification(payload)

    async def notify_waitlist_available(self, book: BookTable, user_id: int):
        """Уведомление из waitlist о доступности книги (ТЗ 3.2.2, 4.3)"""
        msg = (f"🔥 Книга <b>«{book.title}»</b>, которую вы ждали, теперь свободна!\n"
               f"Успейте первым забронировать.")
        payload = {
            "user_id": user_id,
            "type": NotificationType.WAITLIST_AVAILABLE.value,
            "message": msg,
            "book_id": book.id,
            "meta": {}
        }
        await self._send_http_notification(payload)

    async def notify_reservation_approved(self, book: BookTable):
        """
        Уведомление о подтверждении брони админом (ТЗ 3.2.3).

        ВАЖНО: этот метод должен вызываться с актуальным объектом book,
        полученным из БД ПОСЛЕ update_status — иначе return_due_date будет None.
        """
        if book.return_due_date is None:
            # Защитная проверка: если дата всё же None — не крашимся, только логируем
            print(f"⚠️ notify_reservation_approved: book #{book.id} has no return_due_date, skipping date in message")
            date_str = "не указана"
        else:
            date_str = book.return_due_date.strftime("%d.%m.%Y")

        msg = (f"✅ Ваша заявка на книгу <b>«{book.title}»</b> одобрена!\n"
               f"📅 Вернуть до: <b>{date_str}</b>")
        payload = {
            "user_id": book.borrower_id,
            "type": NotificationType.RESERVATION_APPROVED.value,
            "message": msg,
            "book_id": book.id,
            "meta": {"due_date": date_str}
        }
        await self._send_http_notification(payload)

    async def notify_reservation_rejected(self, book: BookTable, user_id: int, reason: str):
        """Уведомление об отклонении брони"""
        msg = f"❌ Заявка на книгу <b>«{book.title}»</b> отклонена.\nПричина: {reason}"
        payload = {
            "user_id": user_id,
            "type": NotificationType.RESERVATION_REJECTED.value,
            "message": msg,
            "book_id": book.id,
            "meta": {"reason": reason}
        }
        await self._send_http_notification(payload)

    async def notify_about_overdue(self, book: BookTable, borrower_username: str = None):
        """Уведомление о просроченной книге (ТЗ 4.2)"""
        if borrower_username:
            borrower_mention = f"<a href=\"tg://user?id={book.borrower_id}\">@{borrower_username}</a>"
        else:
            borrower_mention = f"<a href=\"tg://user?id={book.borrower_id}\">Читатель</a>"

        # Заемщику
        msg_user = (f"🚨 <b>СРОК ВЫШЕЛ!</b>\n\n"
                    f"Книга <b>«{book.title}»</b> просрочена.\n"
                    f"Пожалуйста, верните её как можно скорее.")
        payload_user = {
            "user_id": book.borrower_id,
            "type": NotificationType.OVERDUE.value,
            "message": msg_user,
            "book_id": book.id,
            "meta": {"is_owner": False}
        }
        await self._send_http_notification(payload_user)

        # Владельцу
        msg_owner = (f"🚨 <b>ВНИМАНИЕ!</b>\n\n"
                     f"Книга <b>«{book.title}»</b> просрочена.\n"
                     f"Читатель: {borrower_mention}")
        payload_owner = {
            "user_id": book.owner_id,
            "type": NotificationType.OVERDUE.value,
            "message": msg_owner,
            "book_id": book.id,
            "meta": {"is_owner": True}
        }
        await self._send_http_notification(payload_owner)

    async def notify_borrower_about_due_date(self, book: BookTable, days_left: int):
        """Напоминание о приближающемся сроке возврата"""
        msg = (f"⏰ <b>Напоминание</b>\n\n"
               f"Книга <b>«{book.title}»</b> должна быть возвращена через {days_left} дн.")
        payload = {
            "user_id": book.borrower_id,
            "type": "due_date_reminder",
            "message": msg,
            "book_id": book.id,
            "meta": {"days_left": days_left}
        }
        await self._send_http_notification(payload)

    async def notify_admin_about_reservation(self, book: BookTable, user_id: int, days: int,
                                             requester_username: str = None):
        """Уведомление админу о новой заявке (ТЗ 3.2.3)"""
        if requester_username:
            user_mention = f"<a href=\"tg://user?id={user_id}\">@{requester_username}</a>"
        else:
            user_mention = f"<a href=\"tg://user?id={user_id}\">Пользователь</a>"

        msg = (f"📩 <b>Новая заявка на бронирование!</b>\n\n"
               f"Книга: <b>{book.title}</b>\n"
               f"Автор: {book.author}\n"
               f"Читатель: {user_mention}\n"
               f"Срок: {days} дней")

        payload = {
            "user_id": -1,  # -1 = всем админам
            "type": "admin_reservation_request",
            "message": msg,
            "book_id": book.id,
            "meta": {
                "requester_id": user_id,
                "requester_username": requester_username,
                "days": days
            }
        }
        await self._send_http_notification(payload)


notification_service = NotificationService()


class OverdueChecker:
    """Фоновая задача для проверки просроченных книг (ТЗ 4.2)"""

    def __init__(self):
        # Fix #9: раньше создавался второй независимый connection pool к БД,
        # несмотря на комментарий «переиспользуем engine». Теперь импортируем
        # SessionLocal из session.py — единственный пул на всё приложение.
        from infrastructure.db.session import AsyncSessionLocal
        self.SessionLocal = AsyncSessionLocal

    async def check_overdue(self):
        from sqlalchemy import select
        from infrastructure.db.models import BookTable

        async with self.SessionLocal() as session:
            now = datetime.now()

            # 1. Просроченные — ставим OVERDUE и уведомляем
            result = await session.execute(
                select(BookTable).where(
                    BookTable.status == BookStatus.BORROWED,
                    BookTable.return_due_date < now
                )
            )
            overdue_books = result.scalars().all()

            for book in overdue_books:
                await session.execute(
                    BookTable.__table__.update()
                    .where(BookTable.id == book.id)
                    .values(status=BookStatus.OVERDUE)
                )
                await notification_service.notify_about_overdue(book)
                print(f"⚠️ Book #{book.id} marked as OVERDUE")

            # 2. Напоминания за 3 дня и за 1 день до срока.
            # Чтобы не спамить каждый час — проверяем только узкое окно ±30 минут
            # вокруг точных порогов. За цикл в 1 час окно гарантированно попадёт.
            for days_threshold in (3, 1):
                window_start = now + timedelta(days=days_threshold) - timedelta(minutes=30)
                window_end = now + timedelta(days=days_threshold) + timedelta(minutes=30)

                result2 = await session.execute(
                    select(BookTable).where(
                        BookTable.status == BookStatus.BORROWED,
                        BookTable.return_due_date >= window_start,
                        BookTable.return_due_date <= window_end,
                    )
                )
                reminder_books = result2.scalars().all()

                for book in reminder_books:
                    delta = book.return_due_date - now
                    days_left = max(1, int(delta.total_seconds() / 86400) + 1)
                    await notification_service.notify_borrower_about_due_date(book, days_left)

            await session.commit()


async def run_background_tasks():
    """Запуск всех фоновых задач"""
    checker = OverdueChecker()
    print(f"🚀 Background tasks started (Push Mode to {BOT_WEBHOOK_URL})...")

    try:
        while True:
            try:
                await checker.check_overdue()
            except Exception as e:
                print(f"❌ Background task error: {e}")
            await asyncio.sleep(3600)  # каждый час
    finally:
        # Fix #9: не вызываем checker.engine.dispose() — движок теперь общий,
        # его закроет lifespan в main.py при штатном завершении приложения.
        await notification_service.close()
        print("🛑 Background tasks stopped, resources released")