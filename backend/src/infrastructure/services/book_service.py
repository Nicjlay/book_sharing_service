from datetime import datetime, timedelta

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.repositories import BookRepository, UserRepository
from infrastructure.services.background_tasks import notification_service
from infrastructure.services.image_service import image_service
from domain.schemas import BookCreate, BookUpdate
from domain.domain_models import BookStatus


class LibraryService:
    def __init__(self, db: AsyncSession):
        self.db        = db
        self.book_repo = BookRepository(db)
        self.user_repo = UserRepository(db)

    # -----------------------------------------------------------------------
    # CRUD Книги
    # -----------------------------------------------------------------------

    async def create_book(self, book_in: BookCreate, photo: UploadFile = None) -> int:
        if photo:
            try:
                book_in.image_path = await image_service.process_and_save(photo)
            except HTTPException:
                raise  # пробрасываем HTTP 413/415 клиенту как есть
            except Exception as e:
                print(f"FAILED TO SAVE IMAGE: {e}")
                book_in.image_path = "books/base_cover.jpg"
        else:
            book_in.image_path = "books/base_cover.jpg"

        if not await self.user_repo.get_by_id(book_in.owner_id):
            raise HTTPException(status_code=404, detail="Владелец не найден")

        book = await self.book_repo.add_book(book_in)
        await self.book_repo.log_history(
            book.id, book_in.owner_id, BookStatus.AVAILABLE,
            comment="Книга добавлена в каталог",
        )

        owner = await self.user_repo.get_by_id(book_in.owner_id)
        # FIX #26: убран лишний @ из owner_username.
        # В шаблоне notify_new_book уже стоит «@{owner_username}»,
        # поэтому передаём только голый username (без @), либо full_name.
        owner_display = owner.username if owner.username else owner.full_name
        try:
            await notification_service.notify_new_book(book, owner_display)
        except Exception as e:
            print(f"⚠️ notify_new_book failed (non-fatal): {e}")

        return book.id

    async def edit_book(self, book_id: int, user_id: int, update_data: BookUpdate):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.owner_id != user_id:
            raise HTTPException(status_code=403, detail="Только владелец может редактировать")

        await self.book_repo.update_book(book_id, update_data)

        changes = []
        if update_data.title:       changes.append("название")
        if update_data.author:      changes.append("автор")
        if update_data.description: changes.append("описание")
        if update_data.genre:       changes.append("жанр")
        if update_data.isbn:        changes.append("ISBN")
        if update_data.image_path:  changes.append("фото")
        comment = f"Изменено: {', '.join(changes)}" if changes else "Редактирование данных"

        await self.book_repo.log_history(book_id, user_id, book.status, comment)
        return await self.book_repo.get_book_by_id(book_id)

    async def delete_book(self, book_id: int, user_id: int):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.owner_id != user_id:
            raise HTTPException(status_code=403, detail="Нет прав")

        if book.status in (BookStatus.BORROWED, BookStatus.RESERVED, BookStatus.OVERDUE):
            raise HTTPException(status_code=400, detail="Нельзя удалить занятую книгу")

        # FIX #14: сначала пишем в историю с актуальным статусом, затем помечаем удалённой.
        # Раньше в историю писался BookStatus.AVAILABLE вне зависимости от реального статуса.
        await self.book_repo.log_history(
            book_id, user_id, BookStatus.DELETED, "Книга удалена (архив)"
        )
        await self.book_repo.soft_delete_book(book_id)

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

        # Атомарная резервация: UPDATE...WHERE status='available' RETURNING id
        reserved = await self.book_repo.try_reserve(book_id, user_id)
        if not reserved:
            raise HTTPException(
                status_code=409,
                detail="Книга занята",
                headers={"X-Reason": "BUSY_OFFER_WAITLIST"},
            )

        wanted_date = (datetime.now() + timedelta(days=days)).date()
        await self.book_repo.log_history(
            book_id, user_id, BookStatus.RESERVED,
            f"Запрос брони до {wanted_date}",
        )

        try:
            requester          = await self.user_repo.get_by_id(user_id)
            requester_username = requester.username if requester else None
        except Exception:
            requester_username = None
        try:
            await notification_service.notify_admin_about_reservation(
                book, user_id, days, requester_username
            )
        except Exception as e:
            print(f"⚠️ notify_admin_about_reservation failed (non-fatal): {e}")

        return {"status": "reserved_pending_approval"}

    async def approve_reservation(self, book_id: int, admin_id: int, due_date: datetime):
        """Шаг 3.2.3 ТЗ: Админ подтверждает выдачу."""
        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.status != BookStatus.RESERVED:
            raise HTTPException(status_code=400, detail="Книга не ожидает подтверждения")

        await self.book_repo.update_status(book_id, BookStatus.BORROWED, due_date=due_date)
        await self.book_repo.log_history(
            book_id, admin_id, BookStatus.BORROWED,
            f"Выдача подтверждена до {due_date.date()}",
        )

        # Перечитываем объект ПОСЛЕ update_status — иначе book.return_due_date == None
        # и notify_reservation_approved упадёт в strftime(None) → AttributeError.
        book = await self.book_repo.get_book_by_id(book_id)
        try:
            await notification_service.notify_reservation_approved(book)
        except Exception as e:
            print(f"⚠️ notify_reservation_approved failed (non-fatal): {e}")

        return book

    async def reject_reservation(self, book_id: int, admin_id: int, reason: str):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        # Сохраняем borrower_id ДО обнуления статуса
        borrower_id = book.borrower_id

        await self.book_repo.update_status(book_id, BookStatus.AVAILABLE)
        await self.book_repo.log_history(
            book_id, admin_id, BookStatus.AVAILABLE,
            f"Отказ в выдаче: {reason}",
        )

        if borrower_id:
            # FIX #11: перечитываем book после update для консистентности,
            # по аналогии с approve_reservation (Fix #1).
            book = await self.book_repo.get_book_by_id(book_id)
            try:
                await notification_service.notify_reservation_rejected(book, borrower_id, reason)
            except Exception as e:
                print(f"⚠️ notify_reservation_rejected failed (non-fatal): {e}")

    # -----------------------------------------------------------------------
    # Бизнес-процесс: Возврат
    # -----------------------------------------------------------------------

    async def return_book(
        self,
        book_id: int,
        user_id: int,
        is_admin: bool,
        photo: UploadFile = None,
    ):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        # FIX #10: убран BookStatus.RESERVED из допустимых статусов возврата.
        # RESERVED — книга ещё не выдана, её возврат должен идти через /reject,
        # иначе владелец получит ложное уведомление «книга возвращена».
        if book.status not in (BookStatus.BORROWED, BookStatus.OVERDUE):
            raise HTTPException(status_code=400, detail="Книга не числится выданной")

        is_borrower = book.borrower_id == user_id
        is_owner    = book.owner_id    == user_id

        if not (is_borrower or is_owner or is_admin):
            raise HTTPException(status_code=403, detail="Нет прав на возврат")

        photo_path = None
        if photo:
            try:
                photo_path = await image_service.process_and_save(photo)
            except HTTPException:
                raise
            except Exception as e:
                print(f"⚠️ Failed to save return photo (non-fatal): {e}")

        previous_owner_id = book.owner_id

        await self.book_repo.update_status(book_id, BookStatus.AVAILABLE)

        actor = "Администратор" if is_admin else ("Владелец" if is_owner else "Читатель")
        await self.book_repo.log_history(
            book_id, user_id, BookStatus.AVAILABLE,
            f"Возврат ({actor})", photo_path,
        )

        # Уведомление владельцу (ТЗ 3.3.3) — только если вернул не сам владелец
        if not is_owner and previous_owner_id:
            try:
                returner          = await self.user_repo.get_by_id(user_id)
                returner_username = returner.username if returner else None
            except Exception:
                returner_username = None
            try:
                await notification_service.notify_owner_about_return(
                    book, user_id, photo_path, returner_username
                )
            except Exception as e:
                print(f"⚠️ notify_owner_about_return failed (non-fatal): {e}")

        # FIX #9: уведомляем только ПЕРВОГО в очереди, остальные ждут следующего возврата.
        # Старый pop_waitlist удалял всех сразу — победить мог только один,
        # но уведомление получали все и все теряли место в очереди.
        first_waiter = await self.book_repo.pop_first_waiter(book_id)
        if first_waiter is not None:
            try:
                await notification_service.notify_waitlist_available(book, first_waiter)
            except Exception as e:
                print(f"⚠️ notify_waitlist_available failed (non-fatal): {e}")

        return {"status": "returned"}