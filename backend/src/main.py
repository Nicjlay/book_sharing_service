from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Header, Form, Body
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import os

from starlette.staticfiles import StaticFiles

from infrastructure.db.session import get_db
from infrastructure.db.repositories import UserRepository, BookRepository
from infrastructure.services.book_service import LibraryService
from infrastructure.services.image_service import image_service
from infrastructure.services.background_tasks import run_background_tasks
from domain.schemas import (
    BookCreate, BookRead, BookUpdate, ReservationRequest,
    ApproveRequest, RejectRequest, BookHistoryRead, UserRead, GenreList,
    NotificationPayload, UserAuthRequest
)
from domain.domain_models import BookStatus
from infrastructure.services.fuzzy_search import search_books as fuzzy_search_books

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path)
# Токен для защиты webhook эндпоинтов
API_TOKEN = os.getenv("API_TOKEN")

app = FastAPI(title="Library Bot API v2.1 (Push Architecture)")

# 1. Определяем базовую папку медиа.
# В Docker контейнере это обычно /app/media
MEDIA_ROOT = Path(os.getenv("MEDIA_UPLOAD_DIR", "/app/media"))
BOOKS_MEDIA_DIR = MEDIA_ROOT / "books"

# 2. Создаем папки при старте, если их нет
BOOKS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# 3. Путь к заглушке (физический)
DEFAULT_IMAGE_PATH = BOOKS_MEDIA_DIR / "base_cover.jpg"

# Простая проверка: если заглушки нет, можно положить туда пустой файл
# или вывести предупреждение в логи при старте
if not DEFAULT_IMAGE_PATH.exists():
    print(f"⚠️ WARNING: Placeholder image not found at {DEFAULT_IMAGE_PATH}")

# 4. Монтируем статику
# Теперь любой файл в /app/media/books/image.jpg
# будет доступен по URL: your-api.com/media/books/image.jpg
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")

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
    user_data: UserAuthRequest,
    db: AsyncSession = Depends(get_db)
):
    repo = UserRepository(db)

    user = await repo.get_or_create_user(
        tg_id=user_data.tg_id,
        full_name=user_data.full_name,
        username=user_data.username,
        is_admin=user_data.is_admin
    )

    return user


@app.get("/users", response_model=List[UserRead])
async def search_users_endpoint(q: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    repo = UserRepository(db)
    return await repo.search_users(q)


# --- CATALOG ---
@app.get("/books/genres", response_model=GenreList)
async def get_genres(db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    genres = await repo.get_all_genres()
    return {"genres": genres}


@app.get("/books", response_model=List[BookRead])
async def list_books(
        status: Optional[BookStatus] = None,
        genre: Optional[str] = None,
        query: Optional[str] = None,
        user_id: Optional[int] = None,
        db: AsyncSession = Depends(get_db)
):
    repo = BookRepository(db)

    if user_id:
        books = await repo.get_my_books(user_id)
        results = []
        for book in books:
            dto = BookRead.model_validate(book)
            if book.owner:
                dto.owner_username = book.owner.username
                dto.owner_full_name = book.owner.full_name
                dto.owner_tg_id = book.owner.tg_id
            results.append(dto)
        return results

    elif query:
        # Загружаем ВСЕ не-удалённые книги, затем ранжируем триграммным поиском
        all_books = await repo.get_books(status=None, genre=None)
        # Конвертируем ORM → dict для fuzzy_search
        book_dicts = []
        orm_map = {}
        for book in all_books:
            d = {"id": book.id, "title": book.title, "author": book.author}
            book_dicts.append(d)
            orm_map[book.id] = book
        # Нечёткий поиск возвращает [(dict, score), ...]
        ranked = fuzzy_search_books(query, book_dicts, threshold=0.20, limit=15)
        results = []
        for book_dict, score in ranked:
            book = orm_map[book_dict["id"]]
            dto = BookRead.model_validate(book)
            if book.owner:
                dto.owner_username = book.owner.username
                dto.owner_full_name = book.owner.full_name
                dto.owner_tg_id = book.owner.tg_id
            results.append(dto)
        return results

    else:
        books = await repo.get_books(status=status, genre=genre)
        results = []
        for book in books:
            dto = BookRead.model_validate(book)
            if book.owner:
                dto.owner_username = book.owner.username
                dto.owner_full_name = book.owner.full_name
                dto.owner_tg_id = book.owner.tg_id
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
        dto.owner_tg_id = book.owner.tg_id
    if book.borrower:
        dto.borrower_username = book.borrower.username
        dto.borrower_full_name = book.borrower.full_name
        dto.borrower_tg_id = book.borrower.tg_id
    return dto


@app.get("/books/{book_id}/history", response_model=List[BookHistoryRead])
async def get_book_history(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    return await repo.get_history(book_id)


# --- WIZARD & MANAGEMENT ---
@app.post("/books", response_model=BookRead, status_code=201)
async def create_book_endpoint(
        title: str = Form(...),
        author: str = Form(...),
        description: str = Form(None),
        genre: str = Form(...),
        owner_id: int = Form(...),
        photo: Optional[UploadFile] = File(None),  # Явно указываем Optional и File
        service: LibraryService = Depends(get_service)
):
    book_in = BookCreate(
        title=title, author=author, description=description,
        genre=genre, owner_id=owner_id
    )
    book_id = await service.create_book(book_in, photo)
    repo = BookRepository(service.db)
    book = await repo.get_book_by_id(book_id)
    return BookRead.model_validate(book)


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
    path = await image_service.process_and_save(file)
    return {"path": path}


# --- FLOW: RESERVATION & RETURN ---

@app.post("/borrowings/request")
async def request_reservation(
        book_id: int,
        payload: ReservationRequest,
        service: LibraryService = Depends(get_service)
):
    return await service.request_reservation(book_id, payload.user_id, payload.days)


@app.post("/borrowings/approve")
async def approve_reservation(
        book_id: int,
        payload: ApproveRequest,
        service: LibraryService = Depends(get_service)
):
    book = await service.approve_reservation(book_id, payload.admin_id, payload.due_date)
    return BookRead.model_validate(book)


@app.post("/borrowings/reject")
async def reject_reservation(
        book_id: int,
        payload: RejectRequest,
        service: LibraryService = Depends(get_service)
):
    await service.reject_reservation(book_id, payload.admin_id, payload.reason)
    return {"status": "rejected"}


@app.post("/borrowings/return")
async def return_book_endpoint(
        book_id: int,
        user_id: int = Form(...),
        is_admin: bool = Form(False),
        photo: Optional[UploadFile] = File(None),
        service: LibraryService = Depends(get_service)
):
    return await service.return_book(book_id, user_id, is_admin, photo)


@app.post("/books/{book_id}/waitlist")
async def join_waitlist(book_id: int, user_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.status == BookStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="Книга доступна, можно брать")

    await repo.add_to_waitlist(book_id, user_id)
    return {"message": "Вы добавлены в лист ожидания"}


# --- BOT WEBHOOK ---
@app.post("/bot/webhook", dependencies=[Depends(verify_bot_token)])
async def bot_webhook(payload: NotificationPayload):
    print(f"📨 Webhook received: {payload.type} for user {payload.user_id}")
    return {"status": "received"}


# --- ADMIN PANEL ---
@app.get("/admin/pending-reservations", response_model=List[BookRead])
async def get_pending_reservations(db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    books = await repo.get_books(status=BookStatus.RESERVED)

    results = []
    for book in books:
        dto = BookRead.model_validate(book)
        if book.owner:
            dto.owner_username = book.owner.username
            dto.owner_full_name = book.owner.full_name
            dto.owner_tg_id = book.owner.tg_id
        if book.borrower:
            dto.borrower_username = book.borrower.username if book.borrower.username else f"ID{book.borrower.id}"
            dto.borrower_full_name = book.borrower.full_name
            dto.borrower_tg_id = book.borrower.tg_id
        results.append(dto)
    return results


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Library API"}