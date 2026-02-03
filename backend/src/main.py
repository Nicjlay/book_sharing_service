from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
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