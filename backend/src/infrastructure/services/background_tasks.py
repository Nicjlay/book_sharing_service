"""
Background tasks и Push-уведомления через HTTP webhook (Push Architecture)
"""
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from domain.domain_models import BookStatus, NotificationType
from infrastructure.db.tables import BookTable
import os

BOT_WEBHOOK_URL = os.getenv("BOT_WEBHOOK_URL", "http://library_bot:8001/webhook")
API_TOKEN = os.getenv("API_TOKEN", "")


class NotificationService:
    """Сервис push-уведомлений: отправляет HTTP POST на бот"""

    def __init__(self):
        self.notifications_queue: list[Dict[str, Any]] = []
        self._max_queue_size = 200

    def _enqueue(self, payload: Dict[str, Any]):
        self.notifications_queue.append({**payload, "_sent_at": datetime.now().isoformat()})
        if len(self.notifications_queue) > self._max_queue_size:
            self.notifications_queue = self.notifications_queue[-self._max_queue_size:]

    def get_notifications(self, user_id: int) -> list[Dict[str, Any]]:
        return [n for n in self.notifications_queue if n.get("user_id") == user_id]

    def clear_notifications(self, user_id: int):
        self.notifications_queue = [n for n in self.notifications_queue if n.get("user_id") != user_id]

    async def _send_http_notification(self, payload: Dict[str, Any]):
        """Отправка уведомления в бот через HTTP"""
        # Конвертируем enum в строку если нужно
        if hasattr(payload.get("type"), "value"):
            payload["type"] = payload["type"].value

        self._enqueue(payload)
        print(f"📡 PUSH to Bot {payload.get('user_id')}: {payload.get('type')}")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
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
        # Формируем упоминание читателя
        if returner_username:
            reader_mention = f"<a href=\"tg://user?id={returner_id}\">@{returner_username}</a>"
        else:
            reader_mention = f"<a href=\"tg://user?id={returner_id}\">Читатель</a>"

        msg = f"📚 Ваша книга <b>«{book.title}»</b> возвращена!\n\nВернул: {reader_mention}"
        if photo_path:
            msg += "\n📸 Приложено фото состояния книги."

        payload = {
            "user_id": book.owner_id,  # users.id = Telegram ID
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

    async def notify_reservation_approved(self, book: BookTable, borrower_username: str = None):
        """Уведомление о подтверждении брони админом (ТЗ 3.2.3)"""
        date_str = book.return_due_date.strftime("%d.%m.%Y")
        msg = (f"✅ Ваша заявка на книгу <b>«{book.title}»</b> одобрена!\n"
               f"📅 Вернуть до: <b>{date_str}</b>")
        payload = {
            "user_id": book.borrower_id,  # users.id = Telegram ID
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
        db_url = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://")
        self.engine = create_async_engine(db_url)
        self.SessionLocal = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def check_overdue(self):
        from sqlalchemy import select
        from infrastructure.db.tables import BookTable

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
                window_end   = now + timedelta(days=days_threshold) + timedelta(minutes=30)

                result2 = await session.execute(
                    select(BookTable).where(
                        BookTable.status == BookStatus.BORROWED,
                        BookTable.return_due_date >= window_start,
                        BookTable.return_due_date <= window_end,
                    )
                )
                reminder_books = result2.scalars().all()

                for book in reminder_books:
                    # Точное число дней через total_seconds чтобы не показывать 0
                    delta = book.return_due_date - now
                    days_left = max(1, int(delta.total_seconds() / 86400) + 1)
                    await notification_service.notify_borrower_about_due_date(book, days_left)

            await session.commit()


async def run_background_tasks():
    """Запуск всех фоновых задач"""
    checker = OverdueChecker()
    print(f"🚀 Background tasks started (Push Mode to {BOT_WEBHOOK_URL})...")

    while True:
        try:
            await checker.check_overdue()
        except Exception as e:
            print(f"❌ Background task error: {e}")

        await asyncio.sleep(3600)  # каждый час