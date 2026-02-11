from datetime import datetime, timedelta
from typing import List, Dict
import asyncio
from infrastructure.db.session import AsyncSessionLocal
from infrastructure.db.repositories import BookRepository
from infrastructure.db.models import BookTable
from domain.domain_models import BookStatus
from sqlalchemy import select, and_

class NotificationService:
    """
    В реальном проекте здесь будет отправка в Telegram через Bot API или RabbitMQ.
    Здесь имитация (in-memory queue).
    """
    def __init__(self):
        self.notifications_queue: List[Dict] = []

    async def notify_owner_about_return(self, book: BookTable, returner_id: int, photo_path: str = None):
        msg = f"📚 Ваша книга '{book.title}' возвращена (ID {returner_id})."
        if photo_path:
            msg += " 📸 Приложено фото состояния."
        else:
            msg += " Фото не приложено."

        self._add(book.owner_id, "book_returned", msg, book.id)

    async def notify_waitlist_available(self, book: BookTable, user_id: int):
        # ТЗ 4.3
        msg = f"🔥 Книга '{book.title}', которую вы ждали, теперь свободна! Успейте забронировать."
        self._add(user_id, "waitlist_available", msg, book.id)

    async def notify_reservation_approved(self, book: BookTable):
        date_str = book.return_due_date.strftime("%d.%m.%Y")
        msg = f"✅ Ваша заявка на книгу '{book.title}' одобрена! Вернуть до: {date_str}"
        self._add(book.borrower_id, "reservation_approved", msg, book.id)

    async def notify_reservation_rejected(self, book: BookTable, user_id: int, reason: str):
        msg = f"❌ Заявка на '{book.title}' отклонена. Причина: {reason}"
        self._add(user_id, "reservation_rejected", msg, book.id)

    async def notify_about_overdue(self, book: BookTable):
        msg_user = f"🚨 СРОК ВЫШЕЛ: Книга '{book.title}' просрочена! Верните её немедленно."
        self._add(book.borrower_id, "overdue", msg_user, book.id)

        msg_owner = f"🚨 ВНИМАНИЕ: Книга '{book.title}' просрочена читателем (ID {book.borrower_id})."
        self._add(book.owner_id, "overdue_owner", msg_owner, book.id)

    async def notify_borrower_about_due_date(self, book: BookTable, days_left: int):
        msg = f"⏰ Напоминание: Книгу '{book.title}' нужно вернуть через {days_left} дн."
        self._add(book.borrower_id, "due_date_reminder", msg, book.id)

    def _add(self, user_id, type_, message, book_id):
        print(f"📡 SEND TO {user_id}: {message}") # Лог в консоль
        self.notifications_queue.append({
            "user_id": user_id,
            "type": type_,
            "message": message,
            "book_id": book_id,
            "timestamp": datetime.now()
        })

    def get_notifications(self, user_id: int) -> List[Dict]:
        return [n for n in self.notifications_queue if n["user_id"] == user_id]

    def clear_notifications(self, user_id: int):
        self.notifications_queue = [n for n in self.notifications_queue if n["user_id"] != user_id]

notification_service = NotificationService()

# --- Checker Logic stays mostly the same, ensuring it uses correct Repo methods ---
class OverdueChecker:
    def __init__(self, notification_service: NotificationService):
        self.notification_service = notification_service

    async def check_overdue_books(self):
        async with AsyncSessionLocal() as session:
            repo = BookRepository(session)
            # ТЗ 4.2: Просрочена - статус Overdue
            stmt = select(BookTable).where(
                BookTable.status == BookStatus.BORROWED,
                BookTable.return_due_date < datetime.now()
            )
            res = await session.execute(stmt)
            overdue_books = res.scalars().all()

            for book in overdue_books:
                # Меняем статус на OVERDUE
                await repo.update_status(book.id, BookStatus.OVERDUE, borrower_id=book.borrower_id, due_date=book.return_due_date)
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
    print("🚀 Background tasks started...")
    while True:
        try:
            await overdue_checker.check_overdue_books()
            await overdue_checker.check_due_date_reminders()
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"❌ Error in background tasks: {e}")
            await asyncio.sleep(60)