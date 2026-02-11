import asyncio
from datetime import datetime, timedelta
import httpx
from typing import Dict, Any

from sqlalchemy import select, and_

from infrastructure.db.session import AsyncSessionLocal
from infrastructure.db.repositories import BookRepository
from infrastructure.db.models import BookTable
from domain.domain_models import BookStatus, NotificationType

# URL, на который бот слушает входящие уведомления
# В Docker-сети это может быть http://bot_container:8000/webhook
BOT_WEBHOOK_URL = "http://localhost:8001/bot/webhook"


class NotificationService:
    """
    Отправляет уведомления в Bot API через HTTP Webhook.
    """

    async def _send_http_notification(self, payload: Dict[str, Any]):
        """
        Физическая отправка JSON на эндпоинт бота.
        """
        async with httpx.AsyncClient() as client:
            try:
                print(f"📡 PUSH to Bot {payload['user_id']}: {payload['type']}")
                resp = await client.post(BOT_WEBHOOK_URL, json=payload, timeout=5.0)
                if resp.status_code != 200:
                    print(f"⚠️ Bot returned error: {resp.status_code} {resp.text}")
            except Exception as e:
                print(f"❌ Failed to push notification to bot: {e}")
                # TODO: Здесь можно добавить механизм Retry (очередь повторных попыток)

    async def notify_new_book(self, book: Any, owner_username: str):
        """Уведомление в группу о новой книге (ТЗ 3.1.3)"""
        payload = {
            "type": NotificationType.NEW_BOOK,
            "user_id": 0,  # 0 или спец. ID для системных сообщений в группу
            "data": {
                "title": book.title,
                "author": book.author,
                "owner_username": owner_username,
                "book_id": book.id
            }
        }
        await self._send_http_notification(payload)

    async def notify_owner_about_return(self, book: BookTable, returner_id: int, photo_path: str = None):
        msg = f"📚 Ваша книга '{book.title}' возвращена (ID {returner_id})."
        if photo_path:
            msg += " 📸 Приложено фото состояния."

        payload = {
            "user_id": book.owner_id,
            "type": NotificationType.BOOK_RETURNED,
            "message": msg,
            "book_id": book.id,
            "meta": {"photo_path": photo_path, "returner_id": returner_id}
        }
        await self._send_http_notification(payload)

    async def notify_waitlist_available(self, book: BookTable, user_id: int):
        msg = f"🔥 Книга '{book.title}', которую вы ждали, теперь свободна! Успейте забронировать."
        payload = {
            "user_id": user_id,
            "type": NotificationType.WAITLIST_AVAILABLE,
            "message": msg,
            "book_id": book.id,
            "meta": {}
        }
        await self._send_http_notification(payload)

    async def notify_reservation_approved(self, book: BookTable):
        date_str = book.return_due_date.strftime("%d.%m.%Y")
        msg = f"✅ Ваша заявка на книгу '{book.title}' одобрена! Вернуть до: {date_str}"
        payload = {
            "user_id": book.borrower_id,
            "type": NotificationType.RESERVATION_APPROVED,
            "message": msg,
            "book_id": book.id,
            "meta": {"due_date": date_str}
        }
        await self._send_http_notification(payload)

    async def notify_reservation_rejected(self, book: BookTable, user_id: int, reason: str):
        msg = f"❌ Заявка на '{book.title}' отклонена. Причина: {reason}"
        payload = {
            "user_id": user_id,
            "type": NotificationType.RESERVATION_REJECTED,
            "message": msg,
            "book_id": book.id,
            "meta": {"reason": reason}
        }
        await self._send_http_notification(payload)

    async def notify_about_overdue(self, book: BookTable):
        msg_user = f"🚨 СРОК ВЫШЕЛ: Книга '{book.title}' просрочена! Верните её немедленно."
        payload_user = {
            "user_id": book.borrower_id,
            "type": NotificationType.OVERDUE,
            "message": msg_user,
            "book_id": book.id,
            "meta": {"is_owner": False}
        }
        await self._send_http_notification(payload_user)

        msg_owner = f"🚨 ВНИМАНИЕ: Книга '{book.title}' просрочена читателем (ID {book.borrower_id})."
        payload_owner = {
            "user_id": book.owner_id,
            "type": NotificationType.OVERDUE,
            "message": msg_owner,
            "book_id": book.id,
            "meta": {"is_owner": True}
        }
        await self._send_http_notification(payload_owner)

    async def notify_borrower_about_due_date(self, book: BookTable, days_left: int):
        msg = f"⏰ Напоминание: Книгу '{book.title}' нужно вернуть через {days_left} дн."
        payload = {
            "user_id": book.borrower_id,
            "type": "due_date_reminder",
            "message": msg,
            "book_id": book.id,
            "meta": {"days_left": days_left}
        }
        await self._send_http_notification(payload)


notification_service = NotificationService()


class OverdueChecker:
    def __init__(self, service: NotificationService):
        self.notification_service = service

    async def check_overdue_books(self):
        async with AsyncSessionLocal() as session:
            repo = BookRepository(session)
            stmt = select(BookTable).where(
                BookTable.status == BookStatus.BORROWED,
                BookTable.return_due_date < datetime.now()
            )
            res = await session.execute(stmt)
            overdue_books = res.scalars().all()

            for book in overdue_books:
                await repo.update_status(book.id, BookStatus.OVERDUE, borrower_id=book.borrower_id,
                                         due_date=book.return_due_date)
                await self.notification_service.notify_about_overdue(book)

    async def check_due_date_reminders(self, days_before: int = 3):
        async with AsyncSessionLocal() as session:
            reminder_date = datetime.now() + timedelta(days=days_before)
            query = select(BookTable).where(
                and_(
                    BookTable.status == BookStatus.BORROWED,
                    BookTable.return_due_date <= reminder_date,
                    BookTable.return_due_date > datetime.now()
                )
            )
            result = await session.execute(query)
            for book in result.scalars().all():
                days_left = (book.return_due_date - datetime.now()).days
                await self.notification_service.notify_borrower_about_due_date(book, max(0, days_left))


overdue_checker = OverdueChecker(notification_service)


async def run_background_tasks():
    print("🚀 Background tasks started (Push Mode)...")
    while True:
        try:
            await overdue_checker.check_overdue_books()
            await overdue_checker.check_due_date_reminders()
            # Проверка раз в час
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"❌ Error in background tasks: {e}")
            await asyncio.sleep(60)