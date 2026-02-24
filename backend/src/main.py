import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import (
    FastAPI, Depends, HTTPException, UploadFile, File,
    Query, Header, Form, Body, APIRouter,
)
from fastapi.middleware.cors import CORSMiddleware
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
    NotificationPayload, UserAuthRequest, WaitlistRequest,
)
from domain.domain_models import BookStatus
from infrastructure.services.fuzzy_search import search_books as fuzzy_search_books

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path)

# ---------------------------------------------------------------------------
# Обязательные переменные окружения
# ---------------------------------------------------------------------------

# FIX #1 (безопасность): пустой токен означал, что любой запрос с X-API-Token: ""
# проходил проверку. Сервис не должен стартовать без секрета.
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError(
        "API_TOKEN environment variable is required but not set. "
        "Add API_TOKEN=<your-secret> to your .env or docker-compose environment."
    )

# ---------------------------------------------------------------------------
# Lifespan: фоновые задачи и корректный shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    bg_task = asyncio.create_task(run_background_tasks())
    yield
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass
    await notification_service.close()


# ---------------------------------------------------------------------------
# Приложение
# ---------------------------------------------------------------------------

app = FastAPI(title="Library Bot API v2.2", lifespan=lifespan)

# FIX #21: CORS — настраивается через переменную окружения CORS_ORIGINS.
# По умолчанию запрещаем все cross-origin запросы (пустой список).
_cors_origins_raw = os.getenv("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ---------------------------------------------------------------------------
# Медиа-файлы
# ---------------------------------------------------------------------------

MEDIA_ROOT      = Path(os.getenv("MEDIA_UPLOAD_DIR", "/app/media"))
BOOKS_MEDIA_DIR = MEDIA_ROOT / "books"
BOOKS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_IMAGE_PATH = BOOKS_MEDIA_DIR / "base_cover.jpg"
if not DEFAULT_IMAGE_PATH.exists():
    print(f"⚠️ WARNING: Placeholder image not found at {DEFAULT_IMAGE_PATH}")

app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")

# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

# FIX #1 (безопасность): использует secrets.compare_digest — защита от timing attack.
async def verify_bot_token(x_api_token: str = Header(None)) -> bool:
    if not secrets.compare_digest(x_api_token or "", API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True


# ---------------------------------------------------------------------------
# Роутеры
# ---------------------------------------------------------------------------

# FIX #1 (безопасность): ВСЕ защищённые эндпоинты требуют токен.
# Общий Depends на уровне роутера — не нужно добавлять в каждый эндпоинт вручную.
protected = APIRouter(dependencies=[Depends(verify_bot_token)])

# /health намеренно вынесен за пределы protected — нужен для Docker healthcheck.
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Library API"}


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _enrich_book_dto(dto: BookRead, book) -> BookRead:
    """Заполняет денормализованные поля owner/borrower из связанных ORM-объектов."""
    if book.owner:
        dto.owner_username  = book.owner.username
        dto.owner_full_name = book.owner.full_name
        dto.owner_tg_id     = book.owner.tg_id
    if book.borrower:
        dto.borrower_username  = book.borrower.username
        dto.borrower_full_name = book.borrower.full_name
        dto.borrower_tg_id     = book.borrower.tg_id
    return dto


def get_service(db: AsyncSession = Depends(get_db)) -> LibraryService:
    return LibraryService(db)


# ==========================================================================
# USERS
# ==========================================================================

@protected.post("/users/auth", response_model=UserRead)
async def auth_user(
    user_data: UserAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Создаёт или обновляет пользователя по Telegram ID.

    FIX #2: поле is_admin удалено из UserAuthRequest — нельзя самоназначиться админом.
    FIX #20: get_or_create_user не перезаписывает is_admin существующих пользователей.
    """
    repo = UserRepository(db)
    user = await repo.get_or_create_user(
        tg_id=user_data.tg_id,
        full_name=user_data.full_name,
        username=user_data.username,
    )
    return user


@protected.get("/users", response_model=List[UserRead])
async def search_users_endpoint(
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    return await repo.search_users(q)


@protected.post("/users/{tg_id}/set-admin", response_model=UserRead)
async def set_user_admin(
    tg_id: int,
    is_admin: bool = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    """
    FIX #2: единственный эндпоинт для управления правами администратора.
    Защищён токеном. Доступен только из доверенного бот-сервиса.
    """
    repo = UserRepository(db)
    user = await repo.set_admin(tg_id, is_admin)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


# ==========================================================================
# CATALOG
# ==========================================================================

@protected.get("/books/genres", response_model=GenreList)
async def get_genres(db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    genres = await repo.get_all_genres()
    return {"genres": genres}


@protected.get("/books", response_model=List[BookRead])
async def list_books(
    status:  Optional[BookStatus] = None,
    genre:   Optional[str]        = None,
    query:   Optional[str]        = None,
    user_id: Optional[int]        = None,
    limit:   int = Query(default=50, ge=1, le=200),
    offset:  int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    repo = BookRepository(db)

    if user_id:
        books = await repo.get_my_books(user_id)
        return [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]

    if query:
        # FIX #15: используем облегчённый запрос — только id/title/author без JOIN-ов.
        # Для fuzzy-поиска полные ORM-объекты с owner/borrower не нужны.
        book_dicts = await repo.get_books_lightweight(limit=2000)
        id_to_dict = {d["id"]: d for d in book_dicts}

        ranked = fuzzy_search_books(query, book_dicts, threshold=0.20, limit=15)
        if not ranked:
            return []

        # Полные объекты загружаем только для найденных книг (максимум 15)
        matched_ids = [d["id"] for d, _ in ranked]
        full_books_list = []
        for book_id in matched_ids:
            book = await repo.get_book_by_id(book_id)
            if book:
                full_books_list.append(book)

        return [_enrich_book_dto(BookRead.model_validate(b), b) for b in full_books_list]

    books = await repo.get_books(status=status, genre=genre, limit=limit, offset=offset)
    return [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]


@protected.get("/books/{book_id}", response_model=BookRead)
async def get_book_detail(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.is_deleted:
        raise HTTPException(status_code=404, detail="Book not found")
    return _enrich_book_dto(BookRead.model_validate(book), book)


@protected.get("/books/{book_id}/history", response_model=List[BookHistoryRead])
async def get_book_history(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    return await repo.get_history(book_id)


# ==========================================================================
# WIZARD & MANAGEMENT
# ==========================================================================

@protected.post("/books", response_model=BookRead, status_code=201)
async def create_book_endpoint(
    title:       str           = Form(...),
    author:      str           = Form(...),
    description: str           = Form(None),
    genre:       str           = Form(...),
    owner_id:    int           = Form(...),
    photo: Optional[UploadFile] = File(None),
    service: LibraryService    = Depends(get_service),
):
    book_in = BookCreate(
        title=title, author=author, description=description,
        genre=genre, owner_id=owner_id,
    )
    book_id = await service.create_book(book_in, photo)
    repo  = BookRepository(service.db)
    book  = await repo.get_book_by_id(book_id)
    return _enrich_book_dto(BookRead.model_validate(book), book)


@protected.patch("/books/{book_id}", response_model=BookRead)
async def edit_book_endpoint(
    book_id: int,
    update:  BookUpdate,
    user_id: int = Query(...),
    service: LibraryService = Depends(get_service),
):
    book = await service.edit_book(book_id, user_id, update)
    return _enrich_book_dto(BookRead.model_validate(book), book)


@protected.delete("/books/{book_id}")
async def delete_book_endpoint(
    book_id: int,
    user_id: int = Query(...),
    service: LibraryService = Depends(get_service),
):
    await service.delete_book(book_id, user_id)
    return {"status": "deleted"}


@protected.post("/media/upload")
async def upload_media(file: UploadFile = File(...)):
    # image_service теперь сам бросает HTTP 413/415 при нарушениях
    path = await image_service.process_and_save(file)
    return {"path": path}


# ==========================================================================
# FLOW: RESERVATION & RETURN
# ==========================================================================

@protected.post("/books/{book_id}/reserve")
async def request_reservation(
    book_id: int,
    payload: ReservationRequest,
    service: LibraryService = Depends(get_service),
):
    return await service.request_reservation(book_id, payload.user_id, payload.days)


@protected.post("/books/{book_id}/approve", response_model=BookRead)
async def approve_reservation(
    book_id: int,
    payload: ApproveRequest,
    service: LibraryService = Depends(get_service),
):
    book = await service.approve_reservation(book_id, payload.admin_id, payload.due_date)
    return _enrich_book_dto(BookRead.model_validate(book), book)


@protected.post("/books/{book_id}/reject")
async def reject_reservation(
    book_id: int,
    payload: RejectRequest,
    service: LibraryService = Depends(get_service),
):
    await service.reject_reservation(book_id, payload.admin_id, payload.reason)
    return {"status": "rejected"}


@protected.post("/books/{book_id}/return")
async def return_book_endpoint(
    book_id:  int,
    user_id:  int  = Form(...),
    is_admin: bool = Form(False),
    photo: Optional[UploadFile] = File(None),
    service: LibraryService = Depends(get_service),
):
    return await service.return_book(book_id, user_id, is_admin, photo)


@protected.post("/books/{book_id}/waitlist")
async def join_waitlist(
    book_id: int,
    # FIX #23: user_id перенесён из query-параметра в тело запроса —
    # идентификатор пользователя не должен попадать в URL и access-логи nginx.
    payload: WaitlistRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.status == BookStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="Книга доступна, можно брать")

    added = await repo.add_to_waitlist(book_id, payload.user_id)

    # FIX #25: различаем «добавлен» (201) и «уже в очереди» (200).
    if added:
        return {"message": "Вы добавлены в лист ожидания", "added": True}
    return {"message": "Вы уже в листе ожидания", "added": False}


@protected.delete("/books/{book_id}/waitlist")
async def leave_waitlist(
    book_id: int,
    # FIX #24: добавлен эндпоинт выхода из waitlist.
    payload: WaitlistRequest,
    db: AsyncSession = Depends(get_db),
):
    """Пользователь может отписаться от уведомлений по книге."""
    repo = BookRepository(db)
    await repo.remove_from_waitlist(book_id, payload.user_id)
    return {"message": "Вы удалены из листа ожидания"}


# ==========================================================================
# BOT WEBHOOK (входящие события от бота)
# ==========================================================================

# Уже защищён через зависимость в роутере `protected`.
@protected.post("/bot/webhook")
async def bot_webhook(payload: NotificationPayload):
    print(f"📨 Webhook received: {payload.type} for user {payload.user_id}")
    return {"status": "received"}


# ==========================================================================
# ADMIN PANEL
# ==========================================================================

@protected.get("/admin/pending-reservations", response_model=List[BookRead])
async def get_pending_reservations(db: AsyncSession = Depends(get_db)):
    repo  = BookRepository(db)
    books = await repo.get_books(status=BookStatus.RESERVED)
    return [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]


# ---------------------------------------------------------------------------
# Подключаем защищённый роутер к приложению
# ---------------------------------------------------------------------------
app.include_router(protected)