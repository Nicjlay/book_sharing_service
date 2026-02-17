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
        self.db = db
        self.book_repo = BookRepository(db)
        self.user_repo = UserRepository(db)

    # --- CRUD Книги ---

    async def create_book(self, book_in: BookCreate, photo: UploadFile = None) -> int:
        # 1. Если прислали фото, сохраняем его
        if photo:
            try:
                book_in.image_path = await image_service.process_and_save(photo)
            except Exception as e:
                # В Docker здесь часто падают права доступа. Смотрите логи!
                print(f"FAILED TO SAVE IMAGE: {e}")
                book_in.image_path = 'books/base_cover.jpg'
        else:
            book_in.image_path = "books/base_cover.jpg"
        # 2. Проверяем владельца
        if not await self.user_repo.get_by_id(book_in.owner_id):
            raise HTTPException(status_code=404, detail="Владелец не найден")

        # 3. Создаем книгу
        book = await self.book_repo.add_book(book_in)

        await self.book_repo.log_history(
            book.id, book_in.owner_id, BookStatus.AVAILABLE,
            comment="Книга добавлена в каталог"
        )

        owner = await self.user_repo.get_by_id(book_in.owner_id)
        owner_username = f"@{owner.username}" if owner.username else owner.full_name
        try:
            await notification_service.notify_new_book(book, owner_username)
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
        if update_data.title: changes.append("название")
        if update_data.author: changes.append("автор")
        if update_data.description: changes.append("описание")
        if update_data.genre: changes.append("жанр")
        if update_data.image_path: changes.append("фото")
        comment = f"Изменено: {', '.join(changes)}" if changes else "Редактирование данных"

        await self.book_repo.log_history(book_id, user_id, book.status, comment)
        return await self.book_repo.get_book_by_id(book_id)

    async def delete_book(self, book_id: int, user_id: int):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.owner_id != user_id:
            raise HTTPException(status_code=403, detail="Нет прав")

        if book.status in [BookStatus.BORROWED, BookStatus.RESERVED]:
            raise HTTPException(status_code=400, detail="Нельзя удалить занятую книгу")

        await self.book_repo.soft_delete_book(book_id)
        await self.book_repo.log_history(book_id, user_id, BookStatus.AVAILABLE, "Книга удалена (архив)")

    # --- Бизнес-процесс: Бронирование ---

    async def request_reservation(self, book_id: int, user_id: int, days: int):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.is_deleted:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        if book.status != BookStatus.AVAILABLE:
            # ТЗ 3.2: Если занята -> предлагаем Waitlist
            # Возвращаем спец код, чтобы фронт показал кнопку "Уведомить меня"
            raise HTTPException(
                status_code=409,
                detail="Книга занята",
                headers={"X-Reason": "BUSY_OFFER_WAITLIST"}
            )

        if book.owner_id == user_id:
            raise HTTPException(status_code=400, detail="Нельзя бронировать свою книгу")

        # Меняем статус на RESERVED (ждет админа)
        await self.book_repo.update_status(book_id, BookStatus.RESERVED, borrower_id=user_id)

        # Расчет желаемой даты (информативно)
        wanted_date = (datetime.now() + timedelta(days=days)).date()

        await self.book_repo.log_history(
            book_id, user_id, BookStatus.RESERVED,
            f"Запрос брони до {wanted_date}"
        )

        # Отправляем уведомление админу о новой заявке (ТЗ 3.2.3)
        try:
            await notification_service.notify_admin_about_reservation(book, user_id, days)
        except Exception as e:
            print(f"⚠️ notify_admin_about_reservation failed (non-fatal): {e}")

        return {"status": "reserved_pending_approval"}

    async def approve_reservation(self, book_id: int, admin_id: int, due_date: datetime):
        """Шаг 3.2.3 ТЗ: Админ подтверждает выдачу"""
        book = await self.book_repo.get_book_by_id(book_id)
        if not book or book.status != BookStatus.RESERVED:
            raise HTTPException(status_code=400, detail="Книга не ожидает подтверждения")

        await self.book_repo.update_status(book_id, BookStatus.BORROWED, due_date=due_date)

        await self.book_repo.log_history(
            book_id, admin_id, BookStatus.BORROWED,
            f"Выдача подтверждена до {due_date.date()}"
        )

        # Уведомляем пользователя об одобрении (ТЗ 3.2.3)
        try:
            await notification_service.notify_reservation_approved(book)
        except Exception as e:
            print(f"⚠️ notify_reservation_approved failed (non-fatal): {e}")
        return book

    async def reject_reservation(self, book_id: int, admin_id: int, reason: str):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        borrower_id = book.borrower_id

        # Сброс в Available
        await self.book_repo.update_status(book_id, BookStatus.AVAILABLE)

        await self.book_repo.log_history(
            book_id, admin_id, BookStatus.AVAILABLE,
            f"Отказ в выдаче: {reason}"
        )

        # Уведомляем пользователя об отказе
        if borrower_id:
            try:
                await notification_service.notify_reservation_rejected(book, borrower_id, reason)
            except Exception as e:
                print(f"⚠️ notify_reservation_rejected failed (non-fatal): {e}")

    # --- Бизнес-процесс: Возврат ---

    async def return_book(self, book_id: int, user_id: int, is_admin: bool, photo: UploadFile = None):
        book = await self.book_repo.get_book_by_id(book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Книга не найдена")

        # Проверка прав: вернуть может заемщик, владелец или админ
        is_borrower = book.borrower_id == user_id
        is_owner = book.owner_id == user_id

        if not (is_borrower or is_owner or is_admin):
            raise HTTPException(status_code=403, detail="Нет прав на возврат")

        # Обработка фото (ТЗ 3.3.2)
        photo_path = None
        if photo:
            photo_path = await image_service.process_and_save(photo)

        previous_borrower = book.borrower_id

        # 1. Меняем статус
        await self.book_repo.update_status(book_id, BookStatus.AVAILABLE)

        # 2. Лог
        actor = "Администратор" if is_admin else ("Владелец" if is_owner else "Читатель")
        comment = f"Возврат ({actor})"
        await self.book_repo.log_history(
            book_id, user_id, BookStatus.AVAILABLE,
            comment, photo_path
        )

        # 3. Уведомление Владельцу (ТЗ 3.3.3)
        # Если вернул не владелец, уведомляем его
        if not is_owner and book.owner_id:
            try:
                await notification_service.notify_owner_about_return(book, user_id, photo_path)
            except Exception as e:
                print(f"⚠️ notify_owner_about_return failed (non-fatal): {e}")

        # 4. Обработка Waitlist (ТЗ 3.2.2 -> 4.3)
        waiters = await self.book_repo.get_waitlist_users(book_id)
        for waiter_id in waiters:
            try:
                await notification_service.notify_waitlist_available(book, waiter_id)
            except Exception as e:
                print(f"⚠️ notify_waitlist_available failed (non-fatal): {e}")

        # Очищаем очередь, так как уведомления ушли
        if waiters:
            await self.book_repo.clear_waitlist(book_id)

        return {"status": "returned"}