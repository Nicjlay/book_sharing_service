"""
Сервисный слой библиотеки.

Принцип работы с транзакциями:
Каждый публичный метод — атомарная бизнес-операция.
Все вызовы репозитория выполняются в одной транзакции,
commit() вызывается единожды в конце.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.repositories import BookRepository, UserRepository
from infrastructure.services.background_tasks import notification_service
from infrastructure.services.image_service import image_service
from domain.schemas import BookCreate, BookUpdate
from domain.domain_models import BookStatus
from utils import get_env_int

logger = logging.getLogger(__name__)

MAX_ACTIVE_BORROWS_PER_USER: int = get_env_int(
    "MAX_ACTIVE_BORROWS_PER_USER",
    default=5,
    min_val=1,
    max_val=100,
)


class LibraryService:
    def __init__(self, db: AsyncSession):
        self.db        = db
        self.book_repo = BookRepository(db)
        self.user_repo = UserRepository(db)

    # -----------------------------------------------------------------------
    # CRUD Книги
    # -----------------------------------------------------------------------

    async def create_book(self, book_in: BookCreate, photo: UploadFile = None) -> int:
        owner = await self.user_repo.get_by_id(book_in.owner_id)
        if not owner:
            raise HTTPException(status_code=404, detail="Владелец не найден")

        saved_image_path: str | None = None
        if photo:
            try:
                saved_image_path = await image_service.process_and_save(photo)
            except HTTPException:
                raise
            except Exception as e:
                logger.error("Failed to save image: %s", e)

        final_image_path = saved_image_path or "books/base_cover.jpg"

        try:
            book = await self.book_repo.add_book(book_in, image_path=final_image_path)
            await self.book_repo.log_history(
                book.id, book_in.owner_id, BookStatus.AVAILABLE,
                comment="Книга добавлена в каталог",
            )
            await self.db.commit()
        except Exception:
            if saved_image_path:
                try:
                    await image_service.adelete_image(saved_image_path)
                except Exception as cleanup_err:
                    logger.warning(
                        "Failed to clean up orphan image %s: %s",
                        saved_image_path, cleanup_err,
                    )
            raise

        try:
            await notification_service.notify_new_book(
                book,
                owner_username=owner.username,
                owner_full_name=owner.full_name,
            )
        except Exception as e:
            logger.warning("notify_new_book failed (non-fatal): %s", e)

        return book.id

    async def edit_book(self, book_id: int, user_id: int, update_data: BookUpdate):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        is_admin = await self.user_repo.is_admin(user_id)
        if book.owner_id != user_id and not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Только владелец или администратор может редактировать",
            )

        old_image_path: str | None = None
        if (
            "image_path" in update_data.model_fields_set
            and update_data.image_path != book.image_path
        ):
            old_image_path = book.image_path

        await self.book_repo.update_book(book_id, update_data)

        field_labels = {
            "title": "название", "author": "автор", "description": "описание",
            "genre": "жанр", "isbn": "ISBN", "image_path": "фото",
        }
        changed = [
            label
            for field, label in field_labels.items()
            if field in update_data.model_fields_set
        ]
        comment = f"Изменено: {', '.join(changed)}" if changed else "Редактирование данных"

        await self.book_repo.log_history(book_id, user_id, book.status, comment)
        await self.db.commit()

        if old_image_path:
            try:
                await image_service.adelete_image(old_image_path)
            except Exception as e:
                logger.warning("Failed to delete old image %s: %s", old_image_path, e)

        return await self.book_repo.get_book_by_id(book_id)

    async def delete_book(self, book_id: int, user_id: int):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        is_admin = await self.user_repo.is_admin(user_id)
        if book.owner_id != user_id and not is_admin:
            raise HTTPException(status_code=403, detail="Нет прав")

        if book.status in (BookStatus.BORROWED, BookStatus.RESERVED, BookStatus.OVERDUE):
            raise HTTPException(status_code=400, detail="Нельзя удалить занятую книгу")

        image_path_to_delete = book.image_path

        waitlist_user_ids = await self.book_repo.get_waitlist_users(book_id)

        await self.book_repo.log_history(book_id, user_id, BookStatus.DELETED, "Книга удалена (архив)")
        await self.book_repo.soft_delete_book(book_id)
        await self.book_repo.clear_waitlist(book_id)

        await self.db.commit()

        if image_path_to_delete:
            try:
                await image_service.adelete_image(image_path_to_delete)
            except Exception as e:
                logger.warning(
                    "Failed to delete image for book #%s (%s): %s",
                    book_id, image_path_to_delete, e,
                )

        if waitlist_user_ids:
            results = await asyncio.gather(
                *[
                    notification_service.notify_book_deleted_from_waitlist(book, waiter_id)
                    for waiter_id in waitlist_user_ids
                ],
                return_exceptions=True,
            )
            for waiter_id, result in zip(waitlist_user_ids, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "notify_book_deleted_from_waitlist(user=%s) failed (non-fatal): %s",
                        waiter_id, result,
                    )

    # -----------------------------------------------------------------------
    # Бизнес-процесс: Бронирование
    # -----------------------------------------------------------------------

    async def request_reservation(self, book_id: int, user_id: int, days: int):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.owner_id == user_id:
            raise HTTPException(status_code=400, detail="Нельзя бронировать свою книгу")

        if book.status != BookStatus.AVAILABLE:
            raise HTTPException(
                status_code=409,
                detail="Книга занята",
                headers={"X-Reason": "BUSY_OFFER_WAITLIST"},
            )

        reserved = await self.book_repo.try_reserve(
            book_id, user_id, MAX_ACTIVE_BORROWS_PER_USER
        )
        if not reserved:
            active_count = await self.book_repo.count_active_by_user(user_id)
            if active_count >= MAX_ACTIVE_BORROWS_PER_USER:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Нельзя иметь более {MAX_ACTIVE_BORROWS_PER_USER} книг одновременно. "
                        "Верните уже взятые книги."
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail="Книга занята",
                headers={"X-Reason": "BUSY_OFFER_WAITLIST"},
            )

        actual_count = await self.book_repo.count_active_by_user(user_id)
        if actual_count > MAX_ACTIVE_BORROWS_PER_USER:
            logger.warning(
                "Race condition: user %s has %d active borrows (limit %d). Rolling back.",
                user_id, actual_count, MAX_ACTIVE_BORROWS_PER_USER,
            )

            rolled_back = await self.book_repo.update_status(
                book_id,
                BookStatus.AVAILABLE,
                expected_status=BookStatus.RESERVED,
            )
            if rolled_back:
                await self.book_repo.log_history(
                    book_id, user_id, BookStatus.AVAILABLE,
                    comment=(
                        f"Автоматический откат бронирования: превышен лимит "
                        f"{MAX_ACTIVE_BORROWS_PER_USER} книг (race condition)"
                    ),
                )
            await self.db.commit()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Нельзя иметь более {MAX_ACTIVE_BORROWS_PER_USER} книг одновременно. "
                    "Верните уже взятые книги."
                ),
            )

        wanted_date = (datetime.now(timezone.utc) + timedelta(days=days)).date()
        await self.book_repo.log_history(
            book_id, user_id, BookStatus.RESERVED,
            f"Запрос брони до {wanted_date}",
        )
        await self.db.commit()

        requester_username = None
        try:
            requester = await self.user_repo.get_by_id(user_id)
            requester_username = requester.username if requester else None
        except Exception:
            pass
        try:
            await notification_service.notify_admin_about_reservation(
                book, user_id, days, requester_username
            )
        except Exception as e:
            logger.warning("notify_admin_about_reservation failed (non-fatal): %s", e)

        return {"status": "reserved_pending_approval"}

    async def approve_reservation(self, book_id: int, admin_id: int, due_date: datetime):
        if not await self.user_repo.is_admin(admin_id):
            raise HTTPException(status_code=403, detail="Недостаточно прав: требуется администратор")

        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.status != BookStatus.RESERVED:
            raise HTTPException(status_code=400, detail="Книга не ожидает подтверждения")

        if not book.borrower_id:
            raise HTTPException(
                status_code=422,
                detail="Невозможно подтвердить: у книги отсутствует заёмщик (borrower_id=None).",
            )

        updated = await self.book_repo.update_status(
            book_id, BookStatus.BORROWED, due_date=due_date,
            expected_status=BookStatus.RESERVED,
        )
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="Бронирование уже было обработано другим администратором.",
            )

        await self.book_repo.log_history(
            book_id, admin_id, BookStatus.BORROWED,
            f"Выдача подтверждена до {due_date.date()}",
        )
        await self.db.commit()

        book = await self.book_repo.get_book_by_id(book_id)
        try:
            await notification_service.notify_reservation_approved(book)
        except Exception as e:
            logger.warning("notify_reservation_approved failed (non-fatal): %s", e)

        return book

    async def reject_reservation(self, book_id: int, admin_id: int, reason: str):
        if not await self.user_repo.is_admin(admin_id):
            raise HTTPException(status_code=403, detail="Недостаточно прав: требуется администратор")

        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.status != BookStatus.RESERVED:
            raise HTTPException(
                status_code=400,
                detail="Нечего отклонять: книга не находится в статусе бронирования",
            )

        borrower_id = book.borrower_id

        updated = await self.book_repo.update_status(
            book_id, BookStatus.AVAILABLE,
            expected_status=BookStatus.RESERVED,
        )
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="Бронирование уже было обработано другим администратором.",
            )

        await self.book_repo.log_history(
            book_id, admin_id, BookStatus.AVAILABLE,
            f"Отказ в выдаче: {reason}",
        )

        first_waiter = await self.book_repo.pop_first_waiter(book_id)

        await self.db.commit()

        fresh_book = await self.book_repo.get_book_by_id(book_id)

        if borrower_id:
            try:
                await notification_service.notify_reservation_rejected(fresh_book, borrower_id, reason)
            except Exception as e:
                logger.warning("notify_reservation_rejected failed (non-fatal): %s", e)

        if first_waiter is not None:
            waiter_exists = await self.user_repo.exists(first_waiter)
            if waiter_exists:
                try:
                    await notification_service.notify_waitlist_available(fresh_book, first_waiter)
                except Exception as e:
                    logger.warning(
                        "notify_waitlist_available (after reject) failed (non-fatal): %s", e,
                    )
            else:
                logger.warning(
                    "Skipping waitlist notification for book #%s: user %s not found",
                    book_id, first_waiter,
                )

    # -----------------------------------------------------------------------
    # Бизнес-процесс: Возврат
    # -----------------------------------------------------------------------

    async def return_book(
        self,
        book_id: int,
        user_id: int,
        photo: UploadFile = None,
    ):
        book = await self.book_repo.get_book_by_id(book_id)

        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.status not in (BookStatus.BORROWED, BookStatus.OVERDUE):
            raise HTTPException(status_code=400, detail="Книга не числится выданной")

        is_borrower = book.borrower_id == user_id
        is_owner    = book.owner_id    == user_id
        is_admin    = await self.user_repo.is_admin(user_id)

        if not (is_borrower or is_owner or is_admin):
            raise HTTPException(status_code=403, detail="Нет прав на возврат")

        photo_path = None
        if photo:
            try:
                photo_path = await image_service.process_and_save(photo)
            except HTTPException:
                raise
            except Exception as e:
                logger.warning("Failed to save return photo (non-fatal): %s", e)

        previous_owner_id = book.owner_id
        was_overdue       = book.status == BookStatus.OVERDUE

        updated = await self.book_repo.update_status(
            book_id, BookStatus.AVAILABLE,
            expected_statuses=[BookStatus.BORROWED, BookStatus.OVERDUE],
        )
        if not updated:
            if photo_path:
                try:
                    await image_service.adelete_image(photo_path)
                except Exception:
                    pass
            raise HTTPException(
                status_code=409,
                detail="Книга уже была возвращена другим запросом.",
            )

        actor   = "Администратор" if is_admin else ("Владелец" if is_owner else "Читатель")
        comment = f"Возврат просроченной книги ({actor})" if was_overdue else f"Возврат ({actor})"

        await self.book_repo.log_history(
            book_id, user_id, BookStatus.AVAILABLE, comment, photo_path,
        )

        first_waiter = await self.book_repo.pop_first_waiter(book_id)

        try:
            await self.db.commit()
        except Exception:
            if photo_path:
                try:
                    await image_service.adelete_image(photo_path)
                except Exception as cleanup_err:
                    logger.warning(
                        "Failed to clean up orphan return photo %s: %s",
                        photo_path, cleanup_err,
                    )
            raise

        fresh_book = await self.book_repo.get_book_by_id(book_id)

        if not is_owner and previous_owner_id:
            returner_username = None
            try:
                returner = await self.user_repo.get_by_id(user_id)
                returner_username = returner.username if returner else None
            except Exception:
                pass
            try:
                await notification_service.notify_owner_about_return(
                    fresh_book, user_id, photo_path, returner_username
                )
            except Exception as e:
                logger.warning("notify_owner_about_return failed (non-fatal): %s", e)

        if first_waiter is not None:
            waiter_exists = await self.user_repo.exists(first_waiter)
            if waiter_exists:
                try:
                    await notification_service.notify_waitlist_available(fresh_book, first_waiter)
                except Exception as e:
                    logger.warning("notify_waitlist_available failed (non-fatal): %s", e)
            else:
                logger.warning(
                    "Skipping waitlist notification for book #%s: user %s not found",
                    book_id, first_waiter,
                )

        return {"status": "returned"}
