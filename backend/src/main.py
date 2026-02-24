import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Header, Form, Body
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.staticfiles import StaticFiles

from infrastructure.db.session import get_db
from infrastructure.db.repositories import UserRepository, BookRepository
from infrastructure.services.book_service import LibraryService
from infrastructure.services.image_service import image_service
from infrastructure.services.background_tasks import run_background_tasks, notification_service
from domain.schemas import (
    BookCreate, BookRead, BookUpdate, ReservationRequest,
    ApproveRequest, RejectRequest, BookHistoryRead, UserRead, GenreList,
    NotificationPayload, UserAuthRequest
)
from domain.domain_models import BookStatus
from infrastructure.services.fuzzy_search import search_books as fuzzy_search_books

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path)

# Fix #7: если API_TOKEN не задан — приложение не должно стартовать вообще.
# Пустой токен означает, что любой запрос с заголовком X-API-Token: None
# проходил бы проверку, открывая webhook для всех желающих.
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError(
        "API_TOKEN environment variable is required but not set. "
        "Add API_TOKEN=<your-secret> to your .env or docker-compose environment."
    )

# --------------------------------------------------------------------------
# Lifespan: запуск/остановка фоновых задач
# --------------------------------------------------------------------------

# Fix #15: @app.on_event("startup") помечен deprecated с FastAPI 0.93 и не
# предоставляет хука на shutdown — фоновая задача не могла завершиться
# корректно. Lifespan-контекстный менеджер решает обе проблемы.
@asynccontextmanager
async def lifespan(app: FastAPI):
    bg_task = asyncio.create_task(run_background_tasks())
    yield
    # Штатное завершение: отменяем задачу и закрываем HTTP-клиент уведомлений
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass
    await notification_service.close()


app = FastAPI(title="Library Bot API v2.1 (Push Architecture)", lifespan=lifespan)

# --------------------------------------------------------------------------
# Медиа-файлы
# --------------------------------------------------------------------------

MEDIA_ROOT = Path(os.getenv("MEDIA_UPLOAD_DIR", "/app/media"))
BOOKS_MEDIA_DIR = MEDIA_ROOT / "books"
BOOKS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_IMAGE_PATH = BOOKS_MEDIA_DIR / "base_cover.jpg"
if not DEFAULT_IMAGE_PATH.exists():
    print(f"⚠️ WARNING: Placeholder image not found at {DEFAULT_IMAGE_PATH}")

app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")

# --------------------------------------------------------------------------
# Авторизация webhook
# --------------------------------------------------------------------------

# Fix #6: обычное сравнение != уязвимо к timing attack — по разнице времени
# ответа можно подбирать токен побайтово. secrets.compare_digest работает
# за константное время независимо от содержимого строк.
async def verify_bot_token(x_api_token: str = Header(None)):
    if not secrets.compare_digest(x_api_token or "", API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True


# --------------------------------------------------------------------------
# Вспомогательные функции
# --------------------------------------------------------------------------

# Fix #16: один и тот же блок заполнения owner_*/borrower_* полей повторялся
# в 4+ эндпоинтах. Вынесен в отдельную функцию.
def _enrich_book_dto(dto: BookRead, book) -> BookRead:
    """Заполняет денормализованные поля owner/borrower из связанных объектов."""
    if book.owner:
        dto.owner_username = book.owner.username
        dto.owner_full_name = book.owner.full_name
        dto.owner_tg_id = book.owner.tg_id
    if book.borrower:
        dto.borrower_username = book.borrower.username
        dto.borrower_full_name = book.borrower.full_name
        dto.borrower_tg_id = book.borrower.tg_id
    return dto


def get_service(db: AsyncSession = Depends(get_db)) -> LibraryService:
    return LibraryService(db)


# ==========================================================================
# USERS
# ==========================================================================

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


# ==========================================================================
# CATALOG
# ==========================================================================

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
    # Fix #19: добавлена пагинация — без неё каталог отдавался целиком.
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    repo = BookRepository(db)

    if user_id:
        books = await repo.get_my_books(user_id)
        return [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]

    if query:
        # Fix #13: раньше в Python тянулись ВСЕ книги без ограничений.
        # Добавлен жёсткий потолок в 2000 записей для fuzzy-кандидатов.
        # Для больших каталогов правильное решение — pg_trgm + GIN-индекс
        # на стороне PostgreSQL, тогда первичную фильтрацию делает сама БД.
        all_books = await repo.get_books(status=None, genre=None, limit=2000, offset=0)
        book_dicts = [{"id": b.id, "title": b.title, "author": b.author} for b in all_books]
        orm_map = {b.id: b for b in all_books}

        ranked = fuzzy_search_books(query, book_dicts, threshold=0.20, limit=15)
        results = []
        for book_dict, _score in ranked:
            book = orm_map[book_dict["id"]]
            results.append(_enrich_book_dto(BookRead.model_validate(book), book))
        return results

    books = await repo.get_books(status=status, genre=genre, limit=limit, offset=offset)
    return [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]


@app.get("/books/{book_id}", response_model=BookRead)
async def get_book_detail(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.is_deleted:
        raise HTTPException(status_code=404, detail="Book not found")
    return _enrich_book_dto(BookRead.model_validate(book), book)


@app.get("/books/{book_id}/history", response_model=List[BookHistoryRead])
async def get_book_history(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    return await repo.get_history(book_id)


# ==========================================================================
# WIZARD & MANAGEMENT
# ==========================================================================

@app.post("/books", response_model=BookRead, status_code=201)
async def create_book_endpoint(
    title: str = Form(...),
    author: str = Form(...),
    description: str = Form(None),
    genre: str = Form(...),
    owner_id: int = Form(...),
    photo: Optional[UploadFile] = File(None),
    service: LibraryService = Depends(get_service)
):
    book_in = BookCreate(
        title=title, author=author, description=description,
        genre=genre, owner_id=owner_id
    )
    book_id = await service.create_book(book_in, photo)
    repo = BookRepository(service.db)
    book = await repo.get_book_by_id(book_id)
    return _enrich_book_dto(BookRead.model_validate(book), book)


@app.patch("/books/{book_id}", response_model=BookRead)
async def edit_book_endpoint(
    book_id: int,
    update: BookUpdate,
    user_id: int = Query(...),
    service: LibraryService = Depends(get_service)
):
    book = await service.edit_book(book_id, user_id, update)
    return _enrich_book_dto(BookRead.model_validate(book), book)


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


# ==========================================================================
# FLOW: RESERVATION & RETURN
# Fix #17: book_id был query-параметром в POST-эндпоинтах, что нарушает
# REST-конвенцию. Идентификатор ресурса должен быть в пути URL.
# Новые маршруты: POST /books/{book_id}/reserve|approve|reject|return
# ==========================================================================

@app.post("/books/{book_id}/reserve")
async def request_reservation(
    book_id: int,
    payload: ReservationRequest,
    service: LibraryService = Depends(get_service)
):
    return await service.request_reservation(book_id, payload.user_id, payload.days)


@app.post("/books/{book_id}/approve", response_model=BookRead)
async def approve_reservation(
    book_id: int,
    payload: ApproveRequest,
    service: LibraryService = Depends(get_service)
):
    book = await service.approve_reservation(book_id, payload.admin_id, payload.due_date)
    return _enrich_book_dto(BookRead.model_validate(book), book)


@app.post("/books/{book_id}/reject")
async def reject_reservation(
    book_id: int,
    payload: RejectRequest,
    service: LibraryService = Depends(get_service)
):
    await service.reject_reservation(book_id, payload.admin_id, payload.reason)
    return {"status": "rejected"}


@app.post("/books/{book_id}/return")
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


# ==========================================================================
# BOT WEBHOOK
# ==========================================================================

@app.post("/bot/webhook", dependencies=[Depends(verify_bot_token)])
async def bot_webhook(payload: NotificationPayload):
    print(f"📨 Webhook received: {payload.type} for user {payload.user_id}")
    return {"status": "received"}


# ==========================================================================
# ADMIN PANEL
# ==========================================================================

@app.get("/admin/pending-reservations", response_model=List[BookRead])
async def get_pending_reservations(db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    books = await repo.get_books(status=BookStatus.RESERVED)
    return [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]


# ==========================================================================
# HEALTH
# ==========================================================================

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Library API"}