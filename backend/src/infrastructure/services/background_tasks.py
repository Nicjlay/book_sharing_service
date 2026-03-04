"""
Background tasks и Push-уведомления через HTTP webhook.
"""
import asyncio
import html
import httpx
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
_dotenv_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_dotenv_path)

from utils import get_env_int

from domain.domain_models import BookStatus, NotificationType
from infrastructure.db.models import BookTable, UserTable

logger = logging.getLogger(__name__)

BOT_WEBHOOK_URL = os.getenv("BOT_WEBHOOK_URL", "http://library_bot:8001/webhook")

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError(
        "API_TOKEN environment variable is required for background_tasks. "
        "Add API_TOKEN=<your-secret> to your .env or docker-compose environment."
    )

_CHECK_INTERVAL_SECONDS: int = get_env_int(
    "OVERDUE_CHECK_INTERVAL", default=3600, min_val=60, max_val=86400
)

_REMINDER_TTL_SECONDS = max(5 * 3600, int(_CHECK_INTERVAL_SECONDS * 2.5))

NOTIFY_GROUP_BROADCAST = 0
NOTIFY_ALL_ADMINS      = -1


def _e(text: Any) -> str:
    """HTML-экранирование пользовательских данных в Telegram-сообщениях."""
    return html.escape(str(text)) if text is not None else ""


class NotificationService:
    """Сервис push-уведомлений: отправляет HTTP POST на бот."""

    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None
        self._client_lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        return self._client_lock

    async def _get_http_client(self) -> httpx.AsyncClient:
        async with self._get_lock():
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(timeout=5.0)
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _send_http_notification(self, payload: Dict[str, Any]):
        if hasattr(payload.get("type"), "value"):
            payload["type"] = payload["type"].value

        logger.info("PUSH to Bot user_id=%s type=%s", payload.get("user_id"), payload.get("type"))

        try:
            client   = await self._get_http_client()
            response = await client.post(
                BOT_WEBHOOK_URL,
                json=payload,
                headers={"X-API-Token": API_TOKEN},
            )
            if response.status_code >= 400:
                logger.warning(
                    "Bot returned HTTP %s for notification type=%s user_id=%s. Body: %.200s",
                    response.status_code,
                    payload.get("type"),
                    payload.get("user_id"),
                    response.text,
                )
        except Exception as e:
            logger.error("Bot is not reachable at %s: %s", BOT_WEBHOOK_URL, e)

    async def notify_new_book(
        self,
        book: Any,
        owner_username: Optional[str],
        owner_full_name: str,
    ):
        if owner_username:
            mention = f"@{_e(owner_username)}"
        else:
            mention = _e(owner_full_name)

        msg = (
            f"📖 Новая книга в библиотеке!\n\n"
            f"<b>{_e(book.title)}</b>\n"
            f"✍️ {_e(book.author)}\n"
            f"👤 {mention}"
        )
        await self._send_http_notification({
            "user_id": NOTIFY_GROUP_BROADCAST,
            "type":    NotificationType.NEW_BOOK,
            "message": msg,
            "book_id": book.id,
            "meta":    {
                "owner_username":  owner_username,
                "owner_full_name": owner_full_name,
            },
        })

    async def notify_owner_about_return(
        self,
        book: BookTable,
        returner_id: int,
        photo_path: str = None,
        returner_username: str = None,
    ):
        if returner_username:
            safe_username  = _e(returner_username)
            reader_mention = f"<a href=\"tg://user?id={returner_id}\">@{safe_username}</a>"
        else:
            reader_mention = f"<a href=\"tg://user?id={returner_id}\">Читатель</a>"

        msg = f"📚 Ваша книга <b>«{_e(book.title)}»</b> возвращена!\n\nВернул: {reader_mention}"
        if photo_path:
            msg += "\n📸 Приложено фото состояния книги."

        await self._send_http_notification({
            "user_id": book.owner_id,
            "type":    NotificationType.BOOK_RETURNED,
            "message": msg,
            "book_id": book.id,
            "meta":    {
                "photo_path":        photo_path,
                "returner_id":       returner_id,
                "returner_username": returner_username,
            },
        })

    async def notify_waitlist_available(self, book: BookTable, user_id: int):
        msg = (
            f"🔥 Книга <b>«{_e(book.title)}»</b>, которую вы ждали, теперь свободна!\n"
            f"Успейте первым забронировать."
        )
        await self._send_http_notification({
            "user_id": user_id,
            "type":    NotificationType.WAITLIST_AVAILABLE,
            "message": msg,
            "book_id": book.id,
            "meta":    {},
        })

    async def notify_book_deleted_from_waitlist(self, book: BookTable, user_id: int):
        msg = (
            f"🗑️ Книга <b>«{_e(book.title)}»</b>, которую вы ожидали, была удалена из библиотеки."
        )
        await self._send_http_notification({
            "user_id": user_id,
            "type":    NotificationType.BOOK_DELETED,
            "message": msg,
            "book_id": book.id,
            "meta":    {},
        })

    async def notify_reservation_approved(self, book: BookTable):
        if book.return_due_date is None:
            logger.warning("notify_reservation_approved: book #%s has no return_due_date", book.id)
            date_str = "не указана"
        else:
            date_str = book.return_due_date.strftime("%d.%m.%Y")

        msg = (
            f"✅ Ваша заявка на книгу <b>«{_e(book.title)}»</b> одобрена!\n"
            f"📅 Вернуть до: <b>{date_str}</b>"
        )
        await self._send_http_notification({
            "user_id": book.borrower_id,
            "type":    NotificationType.RESERVATION_APPROVED,
            "message": msg,
            "book_id": book.id,
            "meta":    {"due_date": date_str},
        })

    async def notify_reservation_rejected(self, book: BookTable, user_id: int, reason: str):
        msg = f"❌ Заявка на книгу <b>«{_e(book.title)}»</b> отклонена.\nПричина: {_e(reason)}"
        await self._send_http_notification({
            "user_id": user_id,
            "type":    NotificationType.RESERVATION_REJECTED,
            "message": msg,
            "book_id": book.id,
            "meta":    {"reason": reason},
        })

    async def notify_about_overdue(
        self,
        book: Any,
        borrower_username: str = None,
    ):
        # FIX #1: Гард на None borrower_id.
        #
        # Технически borrower_id должен всегда быть установлен у книги в статусе
        # BORROWED/OVERDUE — это инвариант бизнес-логики. Но при рассинхронизации
        # данных (прямая правка БД, баг миграции) мы не должны падать, пытаясь
        # отправить уведомление на user_id=None. Логируем и уведомляем владельца.
        if book.borrower_id is None:
            logger.error(
                "notify_about_overdue: book #%s has no borrower_id — "
                "skipping borrower notification (data inconsistency)",
                book.id,
            )
            await self._send_http_notification({
                "user_id": book.owner_id,
                "type":    NotificationType.OVERDUE,
                "message": (
                    f"🚨 <b>ВНИМАНИЕ!</b>\n\n"
                    f"Книга <b>«{_e(book.title)}»</b> просрочена, "
                    f"но у неё не установлен заёмщик. Требуется ручная проверка."
                ),
                "book_id": book.id,
                "meta":    {"is_owner": True, "data_error": True},
            })
            return

        if borrower_username:
            safe_username    = _e(borrower_username)
            borrower_mention = f"<a href=\"tg://user?id={book.borrower_id}\">@{safe_username}</a>"
        else:
            borrower_mention = f"<a href=\"tg://user?id={book.borrower_id}\">Читатель</a>"

        # FIX #2: Проверяем результаты gather внутри метода.
        #
        # Старый код использовал return_exceptions=True, но результат не
        # проверялся — ошибка отправки уведомления заёмщику или владельцу
        # была полностью невидима.
        results = await asyncio.gather(
            self._send_http_notification({
                "user_id": book.borrower_id,
                "type":    NotificationType.OVERDUE,
                "message": (
                    f"🚨 <b>СРОК ВЫШЕЛ!</b>\n\n"
                    f"Книга <b>«{_e(book.title)}»</b> просрочена.\n"
                    f"Пожалуйста, верните её как можно скорее."
                ),
                "book_id": book.id,
                "meta":    {"is_owner": False},
            }),
            self._send_http_notification({
                "user_id": book.owner_id,
                "type":    NotificationType.OVERDUE,
                "message": (
                    f"🚨 <b>ВНИМАНИЕ!</b>\n\n"
                    f"Книга <b>«{_e(book.title)}»</b> просрочена.\n"
                    f"Читатель: {borrower_mention}"
                ),
                "book_id": book.id,
                "meta":    {"is_owner": True},
            }),
            return_exceptions=True,
        )

        targets = ["borrower", "owner"]
        for target, result in zip(targets, results):
            if isinstance(result, Exception):
                logger.error(
                    "notify_about_overdue[%s] failed for book #%s: %s",
                    target, book.id, result,
                )

    async def notify_borrower_about_due_date(self, book: Any, days_left: int):
        msg = (
            f"⏰ <b>Напоминание</b>\n\n"
            f"Книга <b>«{_e(book.title)}»</b> должна быть возвращена через {days_left} дн."
        )
        await self._send_http_notification({
            "user_id": book.borrower_id,
            "type":    NotificationType.DUE_DATE_REMINDER,
            "message": msg,
            "book_id": book.id,
            "meta":    {"days_left": days_left},
        })

    async def notify_admin_about_reservation(
        self,
        book: BookTable,
        user_id: int,
        days: int,
        requester_username: str = None,
    ):
        if requester_username:
            safe_username = _e(requester_username)
            user_mention  = f"<a href=\"tg://user?id={user_id}\">@{safe_username}</a>"
        else:
            user_mention = f"<a href=\"tg://user?id={user_id}\">Пользователь</a>"

        msg = (
            f"📩 <b>Новая заявка на бронирование!</b>\n\n"
            f"Книга: <b>{_e(book.title)}</b>\n"
            f"Автор: {_e(book.author)}\n"
            f"Читатель: {user_mention}\n"
            f"Срок: {days} дней"
        )
        await self._send_http_notification({
            "user_id": NOTIFY_ALL_ADMINS,
            "type":    NotificationType.ADMIN_RESERVATION_REQUEST,
            "message": msg,
            "book_id": book.id,
            "meta":    {
                "requester_id":       user_id,
                "requester_username": requester_username,
                "days":               days,
            },
        })


notification_service = NotificationService()


class _BookProxy:
    """
    Лёгкая обёртка над Row из UPDATE...RETURNING или ORM-объектом.
    Гарантирует отсутствие DetachedInstanceError за пределами DB-сессии.
    """
    __slots__ = ("id", "title", "owner_id", "borrower_id", "return_due_date")

    def __init__(self, row):
        self.id              = row.id
        self.title           = row.title
        self.owner_id        = row.owner_id
        self.borrower_id     = row.borrower_id
        self.return_due_date = getattr(row, "return_due_date", None)


class OverdueChecker:
    """Фоновая задача для проверки просроченных книг."""

    def __init__(self):
        from infrastructure.db.session import AsyncSessionLocal
        self.SessionLocal = AsyncSessionLocal
        self._reminded: Dict[tuple, float] = {}

    def _clean_expired(self, now_ts: float) -> None:
        """Удаляет записи старше TTL."""
        cutoff       = now_ts - _REMINDER_TTL_SECONDS
        expired_keys = [k for k, ts in self._reminded.items() if ts < cutoff]
        for k in expired_keys:
            del self._reminded[k]

    async def check_overdue(self):
        from sqlalchemy import select, update as sa_update, insert as sa_insert
        from infrastructure.db.models import BookTable, BookHistoryTable, UserTable

        now    = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        overdue_notifications: list[tuple] = []
        reminder_notifications: list[tuple] = []

        try:
            async with self.SessionLocal() as session:
                overdue_stmt = (
                    sa_update(BookTable)
                    .where(
                        BookTable.status         == BookStatus.BORROWED,
                        BookTable.return_due_date < now,
                        BookTable.is_deleted.is_(False),
                    )
                    .values(status=BookStatus.OVERDUE)
                    .returning(
                        BookTable.id,
                        BookTable.title,
                        BookTable.owner_id,
                        BookTable.borrower_id,
                        BookTable.return_due_date,
                    )
                )
                result             = await session.execute(overdue_stmt)
                newly_overdue_rows = result.all()

                if newly_overdue_rows:
                    history_rows = [
                        {
                            "book_id":   row.id,
                            "user_id":   row.borrower_id or row.owner_id,
                            "status_to": BookStatus.OVERDUE,
                            "comment": (
                                f"Автоматически: срок истёк "
                                f"{row.return_due_date.strftime('%d.%m.%Y')}"
                                if row.return_due_date else "Автоматически: срок истёк"
                            ),
                        }
                        for row in newly_overdue_rows
                    ]
                    await session.execute(sa_insert(BookHistoryTable), history_rows)
                    for row in newly_overdue_rows:
                        logger.warning("Book #%s marked as OVERDUE", row.id)

                for days_threshold in (3, 1):
                    window_start = now + timedelta(days=days_threshold) - timedelta(minutes=30)
                    window_end   = now + timedelta(days=days_threshold) + timedelta(minutes=30)

                    result2 = await session.execute(
                        select(BookTable).where(
                            BookTable.status          == BookStatus.BORROWED,
                            BookTable.return_due_date >= window_start,
                            BookTable.return_due_date <= window_end,
                            BookTable.is_deleted.is_(False),
                        )
                    )
                    reminder_books = result2.scalars().all()

                    for book in reminder_books:
                        key = (book.id, days_threshold)
                        if key in self._reminded:
                            continue
                        delta     = book.return_due_date - now
                        days_left = max(1, int(delta.total_seconds() / 86400) + 1)
                        reminder_notifications.append((_BookProxy(book), days_left, key))

                borrower_usernames: dict[int, str | None] = {}
                if newly_overdue_rows:
                    borrower_ids = list({
                        row.borrower_id
                        for row in newly_overdue_rows
                        if row.borrower_id is not None
                    })
                    if borrower_ids:
                        users_result = await session.execute(
                            select(UserTable.id, UserTable.username)
                            .where(UserTable.id.in_(borrower_ids))
                        )
                        for user_row in users_result:
                            borrower_usernames[user_row.id] = user_row.username

                for row in newly_overdue_rows:
                    overdue_notifications.append((
                        _BookProxy(row),
                        borrower_usernames.get(row.borrower_id),
                    ))

                await session.commit()

        finally:
            # Очистка кэша напоминаний гарантирована через finally.
            self._clean_expired(now_ts)

        # HTTP-вызовы к боту выполняются ПОСЛЕ закрытия DB-сессии.
        if overdue_notifications:
            overdue_results = await asyncio.gather(
                *[
                    notification_service.notify_about_overdue(bp, borrower_username=bu)
                    for bp, bu in overdue_notifications
                ],
                return_exceptions=True,
            )
            for (bp, _), result in zip(overdue_notifications, overdue_results):
                if isinstance(result, Exception):
                    logger.error(
                        "notify_about_overdue failed for book #%s (non-fatal): %s",
                        bp.id, result,
                    )

        if reminder_notifications:
            results = await asyncio.gather(
                *[
                    notification_service.notify_borrower_about_due_date(bp, dl)
                    for bp, dl, _ in reminder_notifications
                ],
                return_exceptions=True,
            )
            for (_, _, key), result in zip(reminder_notifications, results):
                if not isinstance(result, Exception):
                    self._reminded[key] = now_ts
                else:
                    logger.warning(
                        "Reminder notification failed for key %s: %s", key, result
                    )


async def run_background_tasks():
    checker = OverdueChecker()
    logger.info(
        "Background tasks started (Push Mode → %s, interval=%ds, reminder_ttl=%ds)",
        BOT_WEBHOOK_URL, _CHECK_INTERVAL_SECONDS, _REMINDER_TTL_SECONDS,
    )

    try:
        while True:
            cycle_start = time.monotonic()
            try:
                await checker.check_overdue()
            except Exception as e:
                logger.error("Background task error: %s", e, exc_info=True)

            elapsed   = time.monotonic() - cycle_start
            sleep_for = max(0.0, _CHECK_INTERVAL_SECONDS - elapsed)

            if elapsed > _CHECK_INTERVAL_SECONDS * 0.5:
                logger.warning(
                    "check_overdue took %.1fs (>50%% of interval %ds)",
                    elapsed, _CHECK_INTERVAL_SECONDS,
                )

            await asyncio.sleep(sleep_for)
    finally:
        await notification_service.close()
        logger.info("Background tasks stopped, resources released")
