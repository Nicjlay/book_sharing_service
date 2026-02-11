from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, update, delete, and_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from infrastructure.db.models import UserTable, BookTable, BookHistoryTable, WaitlistTable
from domain.domain_models import BookStatus
from domain.schemas import BookCreate, BookUpdate


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(self, tg_id: int, full_name: str, username: str = None):
        user = await self.session.get(UserTable, tg_id)
        if not user:
            user = UserTable(id=tg_id, full_name=full_name, username=username)
            self.session.add(user)
            await self.session.commit()
        else:
            # Обновляем инфу, если сменилась
            if user.full_name != full_name or user.username != username:
                user.full_name = full_name
                user.username = username
                await self.session.commit()
        return user

    async def get_by_id(self, user_id: int):
        return await self.session.get(UserTable, user_id)


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
            image_path=book_data.image_path or "default_cover.jpg",  # Default image handling
            status=BookStatus.AVAILABLE
        )
        self.session.add(new_book)
        await self.session.commit()
        await self.session.refresh(new_book)
        return new_book

    async def get_book_by_id(self, book_id: int) -> Optional[BookTable]:
        # Подгружаем владельца для отображения username
        query = select(BookTable).options(selectinload(BookTable.owner)).where(BookTable.id == book_id)
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

        # Логика сброса/установки полей
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

    async def search_books(self, title: str = None, genre: str = None, status: BookStatus = None) -> List[BookTable]:
        query = select(BookTable).where(BookTable.is_deleted == False).options(selectinload(BookTable.owner))

        if title:
            # Поиск и по автору и по названию (ТЗ 4.3)
            query = query.where(
                (BookTable.title.ilike(f"%{title}%")) |
                (BookTable.author.ilike(f"%{title}%"))
            )
        if genre:
            query = query.where(BookTable.genre == genre)
        if status:
            query = query.where(BookTable.status == status)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_my_books(self, user_id: int) -> List[BookTable]:
        """Мои книги (владелец) + Книги у меня (читатель)"""
        query = select(BookTable).where(
            and_(
                BookTable.is_deleted == False,
                (BookTable.owner_id == user_id) | (BookTable.borrower_id == user_id)
            )
        ).options(selectinload(BookTable.owner))  # Подгружаем связь с владельцем
        result = await self.session.execute(query)
        return result.scalars().all()

    # --- History ---
    async def log_history(self, book_id: int, user_id: int, status_to: BookStatus, comment: str,
                          photo_path: str = None):
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
        query = select(BookHistoryTable).where(BookHistoryTable.book_id == book_id).order_by(
            desc(BookHistoryTable.created_at))
        result = await self.session.execute(query)
        return result.scalars().all()

    # --- Waitlist ---
    async def add_to_waitlist(self, book_id: int, user_id: int):
        # Check exists
        query = select(WaitlistTable).where(
            and_(WaitlistTable.book_id == book_id, WaitlistTable.user_id == user_id)
        )
        res = await self.session.execute(query)
        if not res.scalar_one_or_none():
            self.session.add(WaitlistTable(book_id=book_id, user_id=user_id))
            await self.session.commit()

    async def get_waitlist_users(self, book_id: int) -> List[int]:
        query = select(WaitlistTable.user_id).where(WaitlistTable.book_id == book_id)
        res = await self.session.execute(query)
        return res.scalars().all()

    async def clear_waitlist(self, book_id: int):
        stmt = delete(WaitlistTable).where(WaitlistTable.book_id == book_id)
        await self.session.execute(stmt)
        await self.session.commit()