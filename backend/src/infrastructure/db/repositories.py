from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, update, delete, and_, desc, distinct, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from infrastructure.db.models import UserTable, BookTable, BookHistoryTable, WaitlistTable
from domain.domain_models import BookStatus
from domain.schemas import BookCreate, BookUpdate


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(self, tg_id: int, full_name: str, username: str = None, is_admin: bool = False):
        user = await self.session.get(UserTable, tg_id)
        if not user:
            user = UserTable(id=tg_id, full_name=full_name, username=username, is_admin=is_admin)
            self.session.add(user)
        else:
            # Обновляем данные, если они изменились (включая статус админа из конфига бота)
            user.full_name = full_name
            user.username = username
            user.is_admin = is_admin

        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def get_by_id(self, user_id: int):
        return await self.session.get(UserTable, user_id)

    async def search_users(self, query_str: str = None) -> List[UserTable]:
        """Поиск пользователей для Шага 5 визарда (выбор владельца книги)"""
        stmt = select(UserTable)
        if query_str:
            # Ищем по username или full_name
            stmt = stmt.where(
                or_(
                    UserTable.username.ilike(f"%{query_str}%"),
                    UserTable.full_name.ilike(f"%{query_str}%")
                )
            )
        stmt = stmt.order_by(UserTable.full_name).limit(50)  # Ограничиваем выдачу
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all_admins(self) -> List[UserTable]:
        """Получить всех админов для рассылки уведомлений"""
        stmt = select(UserTable).where(UserTable.is_admin == True)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class BookRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_book(self, book_data: BookCreate) -> BookTable:
        new_book = BookTable(
            title=book_data.title,
            author=book_data.author,
            owner_id=book_data.owner_id,
            description=book_data.description,
            genre=book_data.genre,
            isbn=book_data.isbn,
            image_path=book_data.image_path,
            status=BookStatus.AVAILABLE
        )
        self.session.add(new_book)
        await self.session.commit()
        await self.session.refresh(new_book)
        return new_book

    async def get_book_by_id(self, book_id: int) -> Optional[BookTable]:
        """Получить книгу с загруженными owner и borrower"""
        query = (
            select(BookTable)
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower)
            )
            .where(BookTable.id == book_id)
        )
        result = await self.session.execute(query)
        return result.scalars().one_or_none()

    async def update_book(self, book_id: int, update_data: BookUpdate):
        stmt = (
            update(BookTable)
            .where(BookTable.id == book_id)
            .values(**update_data.model_dump(exclude_unset=True))
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def soft_delete_book(self, book_id: int):
        stmt = update(BookTable).where(BookTable.id == book_id).values(is_deleted=True)
        await self.session.execute(stmt)
        await self.session.commit()

    async def update_status(self, book_id: int, status: BookStatus, borrower_id: int = None, due_date: datetime = None):
        values = {"status": status}

        if status == BookStatus.AVAILABLE:
            values["borrower_id"] = None
            values["return_due_date"] = None
        elif borrower_id is not None:
            values["borrower_id"] = borrower_id

        if due_date is not None:
            values["return_due_date"] = due_date

        stmt = update(BookTable).where(BookTable.id == book_id).values(**values)
        await self.session.execute(stmt)
        await self.session.commit()

    async def get_books(self, status: Optional[BookStatus] = None, genre: Optional[str] = None) -> List[BookTable]:
        """Метод для получения книг с фильтрацией (в т.ч. для админской очереди)"""
        query = (
            select(BookTable)
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower)
            )
            .where(BookTable.is_deleted == False)
        )

        if status:
            query = query.where(BookTable.status == status)
        if genre:
            query = query.where(BookTable.genre == genre)

        query = query.order_by(desc(BookTable.created_at))
        result = await self.session.execute(query)
        return result.scalars().all()

    async def search_books(self, query_str: str) -> List[BookTable]:
        """Полнотекстовый поиск по названию и автору (ТЗ 4.3)"""
        stmt = (
            select(BookTable)
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower)
            )
            .where(
                and_(
                    BookTable.is_deleted == False,
                    or_(
                        BookTable.title.ilike(f"%{query_str}%"),
                        BookTable.author.ilike(f"%{query_str}%")
                    )
                )
            )
            .order_by(desc(BookTable.created_at))
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_my_books(self, user_id: int) -> List[BookTable]:
        """Получить книги пользователя (владелец или заемщик) для раздела 'Мои книги'"""
        query = (
            select(BookTable)
            .where(
                and_(
                    BookTable.is_deleted == False,
                    or_(
                        BookTable.owner_id == user_id,
                        BookTable.borrower_id == user_id
                    )
                )
            )
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower)
            )
            .order_by(desc(BookTable.created_at))
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_genres(self) -> List[str]:
        """Получить уникальные жанры из БД + добавить базовые (ТЗ Шаг 4 визарда)"""
        query = select(distinct(BookTable.genre)).where(BookTable.genre != None)
        result = await self.session.execute(query)
        genres = [g for g in result.scalars().all() if g]

        # Дефолтные жанры, если БД пустая или чтобы они всегда были в топе
        defaults = ["Роман", "Фантастика", "Non-fiction", "Бизнес", "Психология", "Другое"]

        # Объединяем, сохраняя уникальность
        all_genres = list(dict.fromkeys(defaults + genres))
        return all_genres

    # --- History ---
    async def log_history(self, book_id: int, user_id: int, status_to: BookStatus, comment: str,
                          photo_path: str = None):
        """Логирование действий в историю книги (ТЗ 4.4)"""
        entry = BookHistoryTable(
            book_id=book_id,
            user_id=user_id,
            status_to=status_to,
            comment=comment,
            photo_proof_path=photo_path
        )
        self.session.add(entry)
        await self.session.commit()

    async def get_history(self, book_id: int) -> List[BookHistoryTable]:
        """Получить историю книги (ТЗ 4.4: кнопка '📜 История')"""
        query = (
            select(BookHistoryTable)
            .where(BookHistoryTable.book_id == book_id)
            .order_by(desc(BookHistoryTable.created_at))
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    # --- Waitlist ---
    async def add_to_waitlist(self, book_id: int, user_id: int):
        """Добавить пользователя в лист ожидания (ТЗ 3.2.2)"""
        # Проверяем, что пользователь еще не в очереди
        query = select(WaitlistTable).where(
            and_(WaitlistTable.book_id == book_id, WaitlistTable.user_id == user_id)
        )
        res = await self.session.execute(query)
        if not res.scalar_one_or_none():
            self.session.add(WaitlistTable(book_id=book_id, user_id=user_id))
            await self.session.commit()

    async def get_waitlist_users(self, book_id: int) -> List[int]:
        """Получить список пользователей в ожидании (для уведомлений при возврате)"""
        query = select(WaitlistTable.user_id).where(WaitlistTable.book_id == book_id)
        res = await self.session.execute(query)
        return res.scalars().all()

    async def clear_waitlist(self, book_id: int):
        """Очистить лист ожидания после рассылки уведомлений"""
        stmt = delete(WaitlistTable).where(WaitlistTable.book_id == book_id)
        await self.session.execute(stmt)
        await self.session.commit()

    async def remove_from_waitlist(self, book_id: int, user_id: int):
        """Удалить конкретного пользователя из листа ожидания"""
        stmt = delete(WaitlistTable).where(
            and_(WaitlistTable.book_id == book_id, WaitlistTable.user_id == user_id)
        )
        await self.session.execute(stmt)
        await self.session.commit()