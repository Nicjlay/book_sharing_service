from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from domain.schemas import BookCreate, BookRead
from infrastructure.db.session import get_db
from infrastructure.db.repositories import UserRepository, BookRepository
from domain.models import Book, BookStatus

app = FastAPI(title="Book Sharing API")


@app.post("/users/auth")
async def auth_user(tg_id: int, full_name: str, username: str = None, db: AsyncSession = Depends(get_db)):
    repo = UserRepository(db)
    user = await repo.get_or_create_user(tg_id, full_name, username)
    return {"status": "ok", "user_id": user.id}


@app.post("/books/add")
async def add_book(title: str, author: str, owner_id: int, db: AsyncSession = Depends(get_db)):
    user_repo = UserRepository(db)
    # Проверяем, существует ли такой владелец
    query = await user_repo.get_or_create_user(owner_id, "Unknown", "unknown")

    book_repo = BookRepository(db)
    new_book = Book(id=None, title=title, author=author, owner_id=owner_id)
    book_id = await book_repo.add_book(new_book)

    return {"status": "book_added", "book_id": book_id}


@app.get("/books")
async def list_books(db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    books = await repo.get_all_books()
    return books


@app.post("/books", response_model=BookRead, status_code=status.HTTP_201_CREATED)
async def create_book(book_in: BookCreate, db: AsyncSession = Depends(get_db)):
    """Регистрация новой книги в системе."""
    repo = BookRepository(db)
    # Бизнес-логика: перед добавлением книги убедимся, что юзер существует
    user_repo = UserRepository(db)
    user = await user_repo.get_or_create_user(book_in.owner_id, "New User")

    book_id = await repo.add_book(book_in)
    book = await repo.get_book_by_id(book_id)
    return book


@app.patch("/books/{book_id}/reserve")
async def reserve_book(book_id: int, db: AsyncSession = Depends(get_db)):
    """Бронирование книги."""
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)

    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if book.status != BookStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="Книга уже забронирована или выдана")

    await repo.update_book_status(book_id, BookStatus.RESERVED)
    return {"message": "Книга успешно забронирована"}


@app.get("/books/my/{user_id}", response_model=List[BookRead])
async def get_my_shelf(user_id: int, db: AsyncSession = Depends(get_db)):
    """Личный кабинет: список моих книг."""
    repo = BookRepository(db)
    return await repo.get_my_books(user_id)

@app.get("/books/search")
async def search_books(
    title: Optional[str] = None,
    genre: Optional[str] = None,
    status: Optional[BookStatus] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = BookRepository(db)
    # Здесь мы модифицируем запрос в репозитории, добавляя .filter()
    return await repo.search(title=title, genre=genre, status=status)


@app.post("/admin/books/{book_id}/approve-issue")
async def approve_issue(
        book_id: int,
        admin_id: int,
        days: int = 14,
        db: AsyncSession = Depends(get_db)
):
    """
    Админ подтверждает выдачу книги (Пункт 3.2 ТЗ).
    Устанавливает срок возврата и меняет статус на BORROWED.
    """
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)

    if not book or book.status != BookStatus.RESERVED:
        raise HTTPException(status_code=400, detail="Книга не в статусе ожидания выдачи")

    from datetime import timedelta
    due_date = datetime.now() + timedelta(days=days)

    # Обновляем книгу
    book.status = BookStatus.BORROWED
    book.return_due_date = due_date

    # Логируем действие админа
    await repo.log_history(book_id, admin_id, BookStatus.BORROWED, f"Выдана на {days} дней")

    await db.commit()
    return {"status": "success", "return_due_date": due_date}