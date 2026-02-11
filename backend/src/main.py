from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import os

from infrastructure.db.session import get_db
from infrastructure.db.repositories import UserRepository, BookRepository
from infrastructure.services.book_service import LibraryService
from infrastructure.services.image_service import image_service
from infrastructure.services.background_tasks import run_background_tasks
from domain.schemas import (
    BookCreate, BookRead, BookUpdate, ReservationRequest,
    ApproveRequest, RejectRequest, BookHistoryRead, UserRead, GenreList,
    NotificationPayload
)
from domain.domain_models import BookStatus

app = FastAPI(title="Library Bot API v2.1 (Push Architecture)")

# Токен для защиты webhook эндпоинтов
API_TOKEN = os.getenv("API_TOKEN", "your-secret-token-change-in-production")


# Middleware для проверки токена на webhook эндпоинтах
async def verify_bot_token(x_api_token: str = Header(None)):
    if x_api_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True


# Запуск фоновых задач при старте
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_background_tasks())


# Helper to get service
def get_service(db: AsyncSession = Depends(get_db)) -> LibraryService:
    return LibraryService(db)


# --- USERS ---
@app.post("/users/auth", response_model=UserRead)
async def auth_user(
        tg_id: int,
        full_name: str,
        username: str = None,
        is_admin: bool = False,  # Бот передает это, сверив с ENV
        db: AsyncSession = Depends(get_db)
):
    repo = UserRepository(db)
    return await repo.get_or_create_user(tg_id, full_name, username, is_admin)


@app.get("/users", response_model=List[UserRead])
async def search_users_endpoint(q: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """
    Поиск пользователей для выбора владельца книги (Шаг 5 визарда).
    Фильтрует по username или full_name.
    """
    repo = UserRepository(db)
    return await repo.search_users(q)


# --- CATALOG ---
@app.get("/books/genres", response_model=GenreList)
async def get_genres(db: AsyncSession = Depends(get_db)):
    """
    Список жанров для кнопок фильтрации и визарда (Шаг 4).
    """
    repo = BookRepository(db)
    genres = await repo.get_all_genres()
    return {"genres": genres}


@app.get("/books", response_model=List[BookRead])
async def list_books(
        status: Optional[BookStatus] = None,  # Для админа: ?status=reserved
        genre: Optional[str] = None,
        query: Optional[str] = None,  # Поиск по названию/автору
        user_id: Optional[int] = None,  # Мои книги (owner или borrower)
        db: AsyncSession = Depends(get_db)
):
    """
    Универсальный эндпоинт для получения книг с фильтрацией.
    - query: полнотекстовый поиск
    - status: фильтр по статусу (для админа)
    - genre: фильтр по жанру
    - user_id: книги конкретного пользователя (владелец или заемщик)
    """
    repo = BookRepository(db)

    # Мои книги
    if user_id:
        books = await repo.get_my_books(user_id)
    # Поиск
    elif query:
        books = await repo.search_books(query)
    # Фильтры
    else:
        books = await repo.get_books(status=status, genre=genre)

    # Обогащаем данными для UI
    results = []
    for book in books:
        dto = BookRead.model_validate(book)
        if book.owner:
            dto.owner_username = book.owner.username
            dto.owner_full_name = book.owner.full_name
        results.append(dto)
    return results


@app.get("/books/{book_id}", response_model=BookRead)
async def get_book_detail(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.is_deleted:
        raise HTTPException(status_code=404, detail="Book not found")

    dto = BookRead.model_validate(book)
    if book.owner:
        dto.owner_username = book.owner.username
        dto.owner_full_name = book.owner.full_name
    return dto


@app.get("/books/{book_id}/history", response_model=List[BookHistoryRead])
async def get_book_history(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    return await repo.get_history(book_id)


# --- WIZARD & MANAGEMENT ---
@app.post("/books", response_model=BookRead, status_code=201)
async def create_book_endpoint(book_in: BookCreate, service: LibraryService = Depends(get_service)):
    """
    Создание книги через визард.
    Валидация "одного слова" в авторе уже прошла на уровне Pydantic (safety net)
    или была обработана ботом до отправки сюда.
    """
    book_id = await service.create_book(book_in)
    repo = BookRepository(service.db)
    book = await repo.get_book_by_id(book_id)

    dto = BookRead.model_validate(book)
    if book.owner:
        dto.owner_username = book.owner.username
    return dto


@app.patch("/books/{book_id}", response_model=BookRead)
async def edit_book_endpoint(
        book_id: int,
        update: BookUpdate,
        user_id: int = Query(...),
        service: LibraryService = Depends(get_service)
):
    return await service.edit_book(book_id, user_id, update)


@app.delete("/books/{book_id}")
async def delete_book_endpoint(
        book_id: int,
        user_id: int = Query(...),
        service: LibraryService = Depends(get_service)
):
    await service.delete_book(book_id, user_id)
    return {"status": "deleted"}


@app.post("/media/upload")
async def upload_media(file: UploadFile = File(...)):
    """Загрузка изображения обложки (Шаг 3 визарда)"""
    path = await image_service.process_and_save(file)
    return {"path": path}


# --- FLOW: RESERVATION & RETURN ---

@app.post("/borrowings/request")
async def request_reservation(
        book_id: int,
        payload: ReservationRequest,
        service: LibraryService = Depends(get_service)
):
    """
    Запрос на бронирование книги (ТЗ 3.2.2).
    Если книга занята - возвращает 409 с предложением добавиться в waitlist.
    """
    return await service.request_reservation(book_id, payload.user_id, payload.days)


@app.post("/borrowings/approve")
async def approve_reservation(
        book_id: int,
        payload: ApproveRequest,
        service: LibraryService = Depends(get_service)
):
    """Админ подтверждает выдачу книги (ТЗ 3.2.3)"""
    book = await service.approve_reservation(book_id, payload.admin_id, payload.due_date)
    return BookRead.model_validate(book)


@app.post("/borrowings/reject")
async def reject_reservation(
        book_id: int,
        payload: RejectRequest,
        service: LibraryService = Depends(get_service)
):
    """Админ отклоняет заявку на бронирование"""
    await service.reject_reservation(book_id, payload.admin_id, payload.reason)
    return {"status": "rejected"}


@app.post("/borrowings/return")
async def return_book_endpoint(
        book_id: int,
        user_id: int = Query(...),
        is_admin: bool = Query(False),
        photo: Optional[UploadFile] = File(None),
        service: LibraryService = Depends(get_service)
):
    """Возврат книги (ТЗ 3.3)"""
    return await service.return_book(book_id, user_id, is_admin, photo)


@app.post("/books/{book_id}/waitlist")
async def join_waitlist(book_id: int, user_id: int, db: AsyncSession = Depends(get_db)):
    """Добавление в лист ожидания (ТЗ 3.2.2)"""
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.status == BookStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="Книга доступна, можно брать")

    await repo.add_to_waitlist(book_id, user_id)
    return {"message": "Вы добавлены в лист ожидания"}


# --- BOT WEBHOOK ---
@app.post("/bot/webhook", dependencies=[Depends(verify_bot_token)])
async def bot_webhook(payload: NotificationPayload):
    """
    Эндпоинт для приема уведомлений от background_tasks.
    Бот слушает этот эндпоинт и отправляет сообщения пользователям.
    Защищен API токеном.
    """
    # Бот получает payload и отправляет сообщение пользователю
    # Здесь просто подтверждаем получение
    print(f"📨 Webhook received: {payload.type} for user {payload.user_id}")
    return {"status": "received"}


# --- ADMIN PANEL ---
@app.get("/admin/pending-reservations", response_model=List[BookRead])
async def get_pending_reservations(db: AsyncSession = Depends(get_db)):
    """
    Список книг, ожидающих подтверждения выдачи админом.
    Используется для отображения очереди заявок.
    """
    repo = BookRepository(db)
    books = await repo.get_books(status=BookStatus.RESERVED)

    results = []
    for book in books:
        dto = BookRead.model_validate(book)
        if book.owner:
            dto.owner_username = book.owner.username
            dto.owner_full_name = book.owner.full_name
        # Добавляем информацию о том, кто запросил бронь
        if book.borrower:
            # Нужно добавить поле borrower_username в схему
            dto.borrower_username = book.borrower.username if book.borrower.username else f"ID{book.borrower.id}"
            dto.borrower_full_name = book.borrower.full_name
        results.append(dto)
    return results


@app.get("/health")
async def health_check():
    """Health check эндпоинт"""
    return {"status": "ok", "service": "Library API"}