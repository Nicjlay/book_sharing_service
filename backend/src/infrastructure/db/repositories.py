from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from infrastructure.db.models import UserTable, BookTable, BookHistoryTable
from domain.models import Book, BookStatus


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(self, tg_id: int, full_name: str, username: str = None):
        # Проверяем, есть ли юзер в базе
        query = select(UserTable).where(UserTable.id == tg_id)
        result = await self.session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            user = UserTable(id=tg_id, full_name=full_name, username=username)
            self.session.add(user)
            await self.session.commit()
        return user

class BookRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_book(self, book_data: Book) -> int:
        # Превращаем доменную модель в таблицу БД
        new_book = BookTable(
            title=book_data.title,
            author=book_data.author,
            owner_id=book_data.owner_id,
            status=book_data.status,
            image_path=book_data.image_path
        )
        self.session.add(new_book)
        await self.session.commit()
        await self.session.refresh(new_book)
        return new_book.id

    async def get_all_books(self):
        query = select(BookTable)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_book_by_id(self, book_id: int) -> Optional[BookTable]:
        return await self.session.get(BookTable, book_id)

    async def update_book_status(self, book_id: int, new_status: BookStatus):
        stmt = (
            update(BookTable)
            .where(BookTable.id == book_id)
            .values(status=new_status)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def get_my_books(self, user_id: int):
        query = select(BookTable).where(BookTable.id == user_id)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def search_books(self, title: str = None, genre: str = None, status: BookStatus = None):
        """Полнотекстовый поиск и фильтры (Пункт 4.3 ТЗ)"""
        query = select(BookTable)

        if title:
            query = query.where(BookTable.title.ilike(f"%{title}%"))
        if genre:
            query = query.where(BookTable.genre == genre)
        if status:
            query = query.where(BookTable.status == status)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def log_history(self, book_id: int, user_id: int, status: BookStatus, comment: str = None):
        """Запись в журнал истории"""
        history_entry = BookHistoryTable(
            book_id=book_id, user_id=user_id, status_to=status, comment=comment
        )
        self.session.add(history_entry)