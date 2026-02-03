from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from infrastructure.db.models import UserTable, BookTable
from domain.models import Book

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