from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from infrastructure.services.image_service import image_service
from domain.schemas import BookCreate, BookRead
from infrastructure.db.session import get_db
from infrastructure.db.repositories import UserRepository, BookRepository
from domain.models import BookStatus

app = FastAPI(title="Book Sharing API")


# --- СЕКЦИЯ: ПОЛЬЗОВАТЕЛИ ---

@app.post("/users/auth", status_code=status.HTTP_200_OK)
async def auth_user(tg_id: int, full_name: str, username: str = None, db: AsyncSession = Depends(get_db)):
    """Авторизация пользователя (Шаг 0)."""
    repo = UserRepository(db)
    user = await repo.get_or_create_user(tg_id, full_name, username)
    return {"status": "ok", "user_id": user.id}


# --- СЕКЦИЯ: КАТАЛОГ (ПУНКТ 3.2 ТЗ) ---

@app.get("/books", response_model=List[BookRead])
async def list_books(
        title: Optional[str] = Query(None, description="Поиск по названию"),
        genre: Optional[str] = Query(None, description="Фильтр по жанру"),
        status: Optional[BookStatus] = Query(None, description="Фильтр по статусу"),
        db: AsyncSession = Depends(get_db)
):
    """Просмотр каталога с фильтрами (Команда /catalog)."""
    repo = BookRepository(db)
    return await repo.search_books(title=title, genre=genre, status=status)


@app.get("/books/my/{user_id}", response_model=List[BookRead])
async def get_my_shelf(user_id: int, db: AsyncSession = Depends(get_db)):
    """Личный кабинет: список книг, которыми я владею или которые у меня на руках."""
    repo = BookRepository(db)
    return await repo.get_my_books(user_id)


# --- СЕКЦИЯ: РАБОТА С КНИГАМИ (ПУНКТ 2 ТЗ) ---

@app.post("/books", response_model=BookRead, status_code=status.HTTP_201_CREATED)
async def create_book(book_in: BookCreate, db: AsyncSession = Depends(get_db)):
    """Финальный этап добавления книги (Шаг 6 Wizard)."""
    user_repo = UserRepository(db)
    # Проверка: существует ли владелец
    user = await user_repo.get_by_id(book_in.owner_id)
    if not user:
        raise HTTPException(status_code=404, detail="Владелец не найден")

    book_repo = BookRepository(db)
    book_id = await book_repo.add_book(book_in)

    # Автоматически логируем создание в историю
    await book_repo.log_history(book_id, book_in.owner_id, BookStatus.AVAILABLE, "Книга добавлена в каталог")

    return await book_repo.get_book_by_id(book_id)


@app.post("/books/{book_id}/reserve")
async def request_reservation(book_id: int, user_id: int, db: AsyncSession = Depends(get_db)):
    """Запрос на бронирование от пользователя (Пункт 3.2 ТЗ)."""
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)

    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if book.status != BookStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="Книга недоступна для бронирования")

    # Переводим в статус "Забронирована" (Желтый по ТЗ)
    await repo.update_book_status(book_id, BookStatus.RESERVED, borrower_id=user_id)
    await repo.log_history(book_id, user_id, BookStatus.RESERVED, "Запрос на бронирование отправлен админу")

    return {"message": "Запрос отправлен администратору"}


# --- СЕКЦИЯ: АДМИНКА (ПУНКТ 3.3 и 4.2 ТЗ) ---

@app.post("/admin/books/{book_id}/approve")
async def approve_issue(
        book_id: int,
        admin_id: int,
        days: int = 14,
        db: AsyncSession = Depends(get_db)
):
    """Админ подтверждает выдачу (Кнопка ✅ Выдал книгу)."""
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)

    if not book or book.status != BookStatus.RESERVED:
        raise HTTPException(status_code=400, detail="Нет активной заявки на эту книгу")

    due_date = datetime.now() + timedelta(days=days)
    await repo.issue_book(book_id, due_date)  # Метод в репозитории
    await repo.log_history(book_id, admin_id, BookStatus.BORROWED, f"Выдача подтверждена до {due_date.date()}")

    return {"status": "issued", "due_date": due_date}


@app.post("/books/{book_id}/return")
async def return_book(book_id: int, user_id: int, db: AsyncSession = Depends(get_db)):
    """Возврат книги (Пункт 3.3 ТЗ)."""
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)

    if not book or book.borrower_id != user_id:
        raise HTTPException(status_code=400, detail="Вы не можете вернуть книгу, которую не брали")

    await repo.update_book_status(book_id, BookStatus.AVAILABLE, borrower_id=None)
    await repo.log_history(book_id, user_id, BookStatus.AVAILABLE, "Книга возвращена владельцу")

    return {"status": "returned"}


# --- СЕКЦИЯ: МЕДИА (ПУНКТ 2 ШАГ 3 ТЗ) ---

@app.post("/books/{book_id}/image")
async def upload_cover(book_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Загрузка обложки."""
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=400, detail="Неверный формат изображения")

    file_path = await image_service.process_and_save(file)

    repo = BookRepository(db)
    await repo.update_book_image(book_id, file_path)
    return {"path": file_path}