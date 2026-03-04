import asyncio
import contextvars
import logging
import logging.config
import os
import re
import secrets
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set

from dotenv import load_dotenv
from fastapi import (
    FastAPI, Depends, HTTPException, UploadFile, File,
    Query, Header, Form, Body, APIRouter, Request, Path as FPath,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from infrastructure.db.session import get_db, dispose_engine
from infrastructure.db.repositories import UserRepository, BookRepository
from infrastructure.services.book_service import LibraryService
from infrastructure.services.image_service import image_service
from infrastructure.services.background_tasks import run_background_tasks, notification_service
from domain.schemas import (
    BookCreate, BookRead, BookUpdate, ReservationRequest,
    ApproveRequest, RejectRequest, BookHistoryRead, UserRead, GenreList,
    NotificationPayload, UserAuthRequest, WaitlistRequest, SetAdminRequest,
    StatusResponse, WaitlistResponse, Page, USERS_QUERY_MIN_LENGTH,
)
from domain.domain_models import BookStatus
from infrastructure.services.fuzzy_search import search_books as fuzzy_search_books
from infrastructure.services.notification_routes import router as notification_router
from utils import get_env_int

dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path)

# ---------------------------------------------------------------------------
# Обязательные переменные окружения
# ---------------------------------------------------------------------------

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError(
        "API_TOKEN environment variable is required but not set. "
        "Add API_TOKEN=<your-secret> to your .env or docker-compose environment."
    )

# FIX: минимальная длина токена для защиты от тривиально слабых значений.
# Рекомендуется генерировать: python -c "import secrets; print(secrets.token_hex(32))"
_API_TOKEN_MIN_LENGTH = 16
if len(API_TOKEN) < _API_TOKEN_MIN_LENGTH:
    raise RuntimeError(
        f"API_TOKEN is too short ({len(API_TOKEN)} chars). "
        f"Minimum required length is {_API_TOKEN_MIN_LENGTH} characters. "
        'Generate a secure token with: python -c "import secrets; print(secrets.token_hex(32))"'
    )

BOOKS_QUERY_MIN_LENGTH = get_env_int("BOOKS_QUERY_MIN_LENGTH", default=2, min_val=1, max_val=20)
FUZZY_CANDIDATES_LIMIT = get_env_int(
    "FUZZY_CANDIDATES_LIMIT", default=2000, min_val=100, max_val=10_000
)

_JSON_BODY_MAX_BYTES = get_env_int(
    "JSON_BODY_MAX_SIZE", default=1 * 1024 * 1024, min_val=1024, max_val=100 * 1024 * 1024
)

_RATE_LIMIT_REQUESTS = get_env_int("RATE_LIMIT_REQUESTS", default=200, min_val=10, max_val=10_000)
_RATE_LIMIT_WINDOW   = get_env_int("RATE_LIMIT_WINDOW_SECONDS", default=60, min_val=1, max_val=3600)

_TRUSTED_PROXIES_RAW = os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1")
_TRUSTED_PROXIES: Set[str] = {
    ip.strip() for ip in _TRUSTED_PROXIES_RAW.split(",") if ip.strip()
}

_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{1,128}$")


_SEARCH_WORKERS = get_env_int("SEARCH_WORKERS", default=2, min_val=1, max_val=8)
_search_executor: Optional[ThreadPoolExecutor] = None


# ---------------------------------------------------------------------------
# Per-request logging: ContextVar + Filter
# ---------------------------------------------------------------------------

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class _RequestIDFilter(logging.Filter):
    """Добавляет request_id из ContextVar в каждую запись лога."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.request_id = _request_id_var.get()  # type: ignore[attr-defined]
        except LookupError:
            # ContextVar не установлен - используем дефолтное значение
            record.request_id = "-"  # type: ignore[attr-defined]
        return True


# Настройка базового логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s",
)

# Применяем фильтр ко всем существующим и будущим обработчикам
_request_id_filter = _RequestIDFilter()
root_logger = logging.getLogger()
root_logger.addFilter(_request_id_filter)

# Добавляем фильтр ко всем уже существующим обработчикам
for handler in root_logger.handlers:
    handler.addFilter(_RequestIDFilter())

logger = logging.getLogger(__name__)



class RequestIDMiddleware:
    """Pure ASGI middleware: добавляет X-Request-ID к каждому запросу и ответу."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        existing = headers.get(b"x-request-id", b"").decode("ascii", errors="replace").strip()

        if existing and _REQUEST_ID_RE.match(existing):
            request_id = existing
        else:
            request_id = str(uuid.uuid4())

        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id_var.set(request_id)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                extra_headers = list(message.get("headers", []))
                extra_headers.append(
                    (b"x-request-id", request_id.encode("ascii"))
                )
                message = {**message, "headers": extra_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            _request_id_var.reset(token)




class _SlidingWindowCounter:
    """Счётчик запросов в скользящем временном окне."""
    __slots__ = ("_times",)

    def __init__(self):
        self._times: Deque[float] = deque()

    def add_and_count(self, now: float, window: float) -> int:
        cutoff = now - window
        while self._times and self._times[0] < cutoff:
            self._times.popleft()
        self._times.append(now)
        return len(self._times)

    def is_empty(self) -> bool:
        return len(self._times) == 0

    def last_seen(self) -> float:
        return self._times[-1] if self._times else 0.0


class RateLimitMiddleware:
    """
    Pure ASGI middleware: скользящее окно rate limiting по IP.

    /health исключён из rate limiting — Docker/K8s healthcheck вызывает его
    часто и не должен блокироваться.
    """

    _EXEMPT_PATHS           = frozenset({"/health"})
    _CLEANUP_INTERVAL_REQS  = 10_000
    _CLEANUP_STALE_SECONDS  = float(_RATE_LIMIT_WINDOW) * 3

    def __init__(self, app: ASGIApp) -> None:
        self.app      = app
        self._counters: Dict[str, _SlidingWindowCounter] = {}
        self._req_count = 0

    def _get_ip(self, scope: Scope) -> str:
        client    = scope.get("client")
        direct_ip = client[0] if client else "unknown"

        if direct_ip in _TRUSTED_PROXIES:
            headers = dict(scope.get("headers", []))
            xff = headers.get(b"x-forwarded-for", b"").decode("latin-1", errors="replace").strip()
            if xff:
                return xff.split(",")[0].strip()

        return direct_ip

    def _cleanup_stale_counters(self, now: float) -> None:
        cutoff = now - self._CLEANUP_STALE_SECONDS
        stale_keys = [
            ip for ip, counter in self._counters.items()
            if counter.is_empty() or counter.last_seen() < cutoff
        ]
        for key in stale_keys:
            del self._counters[key]
        if stale_keys:
            logger.debug("RateLimitMiddleware: cleaned %d stale counters", len(stale_keys))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self._EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        ip  = self._get_ip(scope)
        now = time.monotonic()

        if ip not in self._counters:
            self._counters[ip] = _SlidingWindowCounter()

        count = self._counters[ip].add_and_count(now, float(_RATE_LIMIT_WINDOW))

        self._req_count += 1
        if self._req_count >= self._CLEANUP_INTERVAL_REQS:
            self._req_count = 0
            self._cleanup_stale_counters(now)

        if count > _RATE_LIMIT_REQUESTS:
            await self._reject_429(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject_429(send: Send) -> None:
        import json
        body = json.dumps({
            "detail": (
                f"Превышен лимит запросов: не более {_RATE_LIMIT_REQUESTS} "
                f"за {_RATE_LIMIT_WINDOW} секунд. Повторите попытку позже."
            )
        }).encode()
        await send({
            "type":    "http.response.start",
            "status":  429,
            "headers": [
                (b"content-type",   b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after",    str(_RATE_LIMIT_WINDOW).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})


# ---------------------------------------------------------------------------
# Middleware: ограничение размера тела запроса
# ---------------------------------------------------------------------------

class MaxBodySizeMiddleware:
    """
    Pure ASGI middleware для ограничения размера тела HTTP-запроса.
    Отклоняет тела > _JSON_BODY_MAX_BYTES байт с HTTP 413.
    Multipart (загрузка файлов) пропускается — там свои ограничения в image_service.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers      = dict(scope.get("headers", []))
        content_type = headers.get(b"content-type", b"").decode("latin-1", errors="replace")
        if "multipart/form-data" in content_type:
            await self.app(scope, receive, send)
            return

        content_length_raw = headers.get(b"content-length")
        if content_length_raw is not None:
            try:
                cl = int(content_length_raw)
            except (ValueError, TypeError):
                cl = 0
            if cl > _JSON_BODY_MAX_BYTES:
                await self._reject_413(send)
                return
            await self.app(scope, receive, send)
            return

        body      = b""
        more_body = True
        while more_body:
            message   = await receive()
            chunk     = message.get("body", b"")
            body     += chunk
            more_body = message.get("more_body", False)
            if len(body) > _JSON_BODY_MAX_BYTES:
                await self._reject_413(send)
                return

        body_consumed = False

        async def buffered_receive() -> dict:
            nonlocal body_consumed
            if not body_consumed:
                body_consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, buffered_receive, send)

    @staticmethod
    async def _reject_413(send: Send) -> None:
        import json
        body = json.dumps({
            "detail": f"Тело запроса превышает допустимый размер ({_JSON_BODY_MAX_BYTES} байт)"
        }).encode()
        await send({
            "type":    "http.response.start",
            "status":  413,
            "headers": [
                (b"content-type",   b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})




@asynccontextmanager
async def lifespan(app: FastAPI):
    global _search_executor
    _search_executor = ThreadPoolExecutor(
        max_workers=_SEARCH_WORKERS,
        thread_name_prefix="fuzzy-search",
    )
    bg_task = asyncio.create_task(run_background_tasks())
    yield

    # 1. Останавливаем фоновую задачу первой, пока пул соединений ещё жив.
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Background task raised exception during shutdown: %s", e, exc_info=True)

    # 2. Закрываем executor fuzzy-поиска.
    if _search_executor is not None:
        await asyncio.to_thread(_search_executor.shutdown, True)
        _search_executor = None

    # 3. Закрываем ThreadPoolExecutor обработки изображений.
    await asyncio.to_thread(image_service.close)

    # 4. Освобождаем пул БД-соединений последним.
    await dispose_engine()


# ---------------------------------------------------------------------------
# Приложение
# ---------------------------------------------------------------------------

app = FastAPI(title="Library Bot API v2.6", lifespan=lifespan)



app.add_middleware(MaxBodySizeMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)

_cors_origins_raw = os.getenv("CORS_ORIGINS", "")
_cors_origins     = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    logger.warning(
        "CORS_ORIGINS is not configured — CORS headers will NOT be sent. "
        "Browser-based clients will fail cross-origin requests. "
        "Set CORS_ORIGINS=https://yourdomain.com in .env to enable CORS."
    )


# ---------------------------------------------------------------------------
# Глобальные обработчики ошибок
# ---------------------------------------------------------------------------

def _get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error(
        "DB Error on %s %s [req=%s]: %s",
        request.method, request.url.path, _get_request_id(request), exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка базы данных. Попробуйте позже."},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s [req=%s]",
        request.method, request.url.path, _get_request_id(request),
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера. Попробуйте позже."},
    )




MEDIA_ROOT      = Path(os.getenv("MEDIA_UPLOAD_DIR", "/app/media"))
BOOKS_MEDIA_DIR = MEDIA_ROOT / "books"
BOOKS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_IMAGE_PATH = BOOKS_MEDIA_DIR / "base_cover.jpg"
if not DEFAULT_IMAGE_PATH.exists():
    logger.warning("Placeholder image not found at %s", DEFAULT_IMAGE_PATH)

app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")

# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

async def verify_bot_token(x_api_token: Optional[str] = Header(None)) -> bool:
    if not secrets.compare_digest(x_api_token or "", API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True


# ---------------------------------------------------------------------------
# Роутеры
# ---------------------------------------------------------------------------

protected = APIRouter(dependencies=[Depends(verify_bot_token)])


@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Проверка живости сервиса и доступности базы данных.
    Намеренно не требует X-API-Token и исключён из rate limiting —
    вызывается Docker/K8s healthcheck.
    """
    try:
        await db.execute(sa_text("SELECT 1"))
    except Exception as e:
        logger.error("Health check DB ping failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "detail": "Database unavailable"},
        )
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


async def _run_fuzzy_search(
    query_stripped: str,
    repo: BookRepository,
    limit: int,
    status: Optional[BookStatus],
    genre: Optional[str],
    user_id: Optional[int] = None,
) -> List[BookRead]:
    """
    Загружает кандидатов из БД и выполняет fuzzy-поиск в ThreadPoolExecutor.
    При использовании пагинация offset не поддерживается (total=-1 в ответе).

    FIX: если _search_executor не инициализирован (e.g. в тестах без lifespan),
    run_in_executor(None, ...) использует дефолтный пул — безопасный fallback.
    """
    book_dicts = await repo.get_books_lightweight(
        limit=FUZZY_CANDIDATES_LIMIT, status=status, genre=genre, user_id=user_id
    )

    loop   = asyncio.get_running_loop()
    ranked = await loop.run_in_executor(
        _search_executor,
        fuzzy_search_books,
        query_stripped,
        book_dicts,
        0.20,
        limit,
    )

    if not ranked:
        return []

    matched_ids     = [d["id"] for d, _ in ranked]
    full_books_list = await repo.get_books_by_ids(matched_ids)
    return [_enrich_book_dto(BookRead.model_validate(b), b) for b in full_books_list]


# ==========================================================================
# USERS
# ==========================================================================

@protected.post("/users/auth", response_model=UserRead)
async def auth_user(
    user_data: UserAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    user = await repo.get_or_create_user(
        tg_id=user_data.tg_id,
        full_name=user_data.full_name,
        username=user_data.username,
    )
    return user


@protected.get("/users", response_model=Page[UserRead])
async def search_users_endpoint(
    q:      Optional[str] = None,
    limit:  int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Поиск пользователей.

    Пустая строка q="" трактуется как отсутствие фильтра
    (возвращает всех пользователей), а не как невалидный запрос.
    Это соответствует семантике: клиент передал пустое поле поиска → показать всех.
    Проверка минимальной длины применяется только к непустым строкам.
    """
    q_stripped: Optional[str] = None
    if q is not None:
        q_stripped = q.strip()
        if not q_stripped:
            q_stripped = None
        elif len(q_stripped) < USERS_QUERY_MIN_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Поисковый запрос должен содержать не менее {USERS_QUERY_MIN_LENGTH} символов",
            )

    repo  = UserRepository(db)
    items = await repo.search_users(q_stripped, limit=limit, offset=offset)
    total = await repo.count_users(q_stripped)
    return Page(items=items, total=total, limit=limit, offset=offset)


@protected.post("/users/{tg_id}/set-admin", response_model=UserRead)
async def set_user_admin(
    tg_id: int,
    payload: SetAdminRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)

    if not await repo.is_admin(payload.requester_id):
        raise HTTPException(
            status_code=403,
            detail="Только администратор может изменять права других пользователей",
        )

    if tg_id == payload.requester_id and not payload.is_admin:
        raise HTTPException(
            status_code=400,
            detail="Администратор не может лишить себя прав. "
                   "Попросите другого администратора изменить ваши права.",
        )

    user = await repo.set_admin(tg_id, payload.is_admin)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


# ==========================================================================
# CATALOG
# ==========================================================================

@protected.get("/books/genres", response_model=GenreList)
async def get_genres(db: AsyncSession = Depends(get_db)):
    repo   = BookRepository(db)
    genres = await repo.get_all_genres()
    return {"genres": genres}


_GENRE_MAX_LENGTH = 50


@protected.get("/books", response_model=Page[BookRead])
async def list_books(
    status:  Optional[BookStatus] = None,
    genre:   Optional[str]        = Query(default=None, max_length=_GENRE_MAX_LENGTH),
    query:   Optional[str]        = None,
    # Telegram ID всегда > 0; значения 0 и отрицательные семантически бессмысленны.
    user_id: Optional[int]        = Query(default=None, gt=0),
    limit:   int = Query(default=50, ge=1, le=200),
    offset:  int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    repo = BookRepository(db)
    genre_normalized: Optional[str] = genre.strip() if genre else None

    if query:
        query_stripped = query.strip()
        if len(query_stripped) < BOOKS_QUERY_MIN_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Поисковый запрос должен содержать не менее {BOOKS_QUERY_MIN_LENGTH} символов",
            )
        items = await _run_fuzzy_search(
            query_stripped, repo, limit, status, genre_normalized, user_id=user_id
        )
        return Page(items=items, total=-1, limit=limit, offset=0)

    if user_id is not None:
        books = await repo.get_my_books(
            user_id, status=status, genre=genre_normalized, limit=limit, offset=offset
        )
        total = await repo.count_books(status=status, genre=genre_normalized, user_id=user_id)
        items = [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]
        return Page(items=items, total=total, limit=limit, offset=offset)

    books = await repo.get_books(status=status, genre=genre_normalized, limit=limit, offset=offset)
    total = await repo.count_books(status=status, genre=genre_normalized)
    items = [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]
    return Page(items=items, total=total, limit=limit, offset=offset)


@protected.get("/books/{book_id}", response_model=BookRead)
async def get_book_detail(
    book_id: int = FPath(..., gt=0, description="ID книги (положительное целое)"),
    db: AsyncSession = Depends(get_db),
):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.is_deleted:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return _enrich_book_dto(BookRead.model_validate(book), book)


@protected.get("/books/{book_id}/history", response_model=Page[BookHistoryRead])
async def get_book_history(
    book_id: int = FPath(..., gt=0, description="ID книги (положительное целое)"),
    limit:   int = Query(default=50, ge=1, le=200),
    offset:  int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    items = await repo.get_history(book_id, limit=limit, offset=offset)
    total = await repo.count_history(book_id)
    return Page(items=items, total=total, limit=limit, offset=offset)


# ==========================================================================
# WIZARD & MANAGEMENT
# ==========================================================================

@protected.post("/books", response_model=BookRead, status_code=201)
async def create_book_endpoint(
    title:       str            = Form(..., min_length=1, max_length=200),
    author:      str            = Form(..., min_length=2, max_length=100),
    description: Optional[str]  = Form(None, max_length=2000),
    genre:       str            = Form(..., max_length=50),
    owner_id:    int            = Form(..., gt=0),
    isbn:        Optional[str]  = Form(None, max_length=20),
    photo: Optional[UploadFile] = File(None),
    service: LibraryService     = Depends(get_service),
):
    book_in = BookCreate(
        title=title, author=author, description=description,
        genre=genre, owner_id=owner_id, isbn=isbn,
    )
    book_id = await service.create_book(book_in, photo)
    repo    = BookRepository(service.db)
    book    = await repo.get_book_by_id(book_id)
    return _enrich_book_dto(BookRead.model_validate(book), book)


class BookEditRequest(BookUpdate):
    """user_id передаётся в теле PATCH — не в query param (mutable операция)."""
    user_id: int = Field(..., gt=0)


@protected.patch("/books/{book_id}", response_model=BookRead)
async def edit_book_endpoint(
    book_id: int = FPath(..., gt=0),
    payload: BookEditRequest = Body(...),
    service: LibraryService = Depends(get_service),
):
    update_data = BookUpdate.model_construct(
        _fields_set=payload.model_fields_set - {"user_id"},
        **payload.model_dump(exclude={"user_id"}, exclude_unset=True),
    )
    book = await service.edit_book(book_id, payload.user_id, update_data)
    return _enrich_book_dto(BookRead.model_validate(book), book)


@protected.delete("/books/{book_id}", response_model=StatusResponse)
async def delete_book_endpoint(
    book_id: int = FPath(..., gt=0),
    user_id: int = Query(..., gt=0, description="Telegram ID пользователя, выполняющего удаление"),
    service: LibraryService = Depends(get_service),
):
    await service.delete_book(book_id, user_id)
    return {"status": "deleted"}


class MediaUploadResponse(BaseModel):
    path: str


@protected.post("/media/upload", response_model=MediaUploadResponse)
async def upload_media(file: UploadFile = File(...)):
    """
    Загрузка медиафайла без привязки к книге.

    ВНИМАНИЕ: файлы не очищаются автоматически если клиент не свяжет их
    с книгой через POST /books или PATCH /books/{id}.
    TODO: добавить фоновую задачу очистки orphan-файлов старше N часов.
    """
    path = await image_service.process_and_save(file)
    return {"path": path}


# ==========================================================================
# FLOW: RESERVATION & RETURN
# ==========================================================================

@protected.post("/books/{book_id}/reserve", response_model=StatusResponse)
async def request_reservation(
    book_id: int = FPath(..., gt=0),
    payload: ReservationRequest = Body(...),
    service: LibraryService = Depends(get_service),
):
    return await service.request_reservation(book_id, payload.user_id, payload.days)


@protected.post("/books/{book_id}/approve", response_model=BookRead)
async def approve_reservation(
    book_id: int = FPath(..., gt=0),
    payload: ApproveRequest = Body(...),
    service: LibraryService = Depends(get_service),
):
    book = await service.approve_reservation(book_id, payload.admin_id, payload.due_date)
    return _enrich_book_dto(BookRead.model_validate(book), book)


@protected.post("/books/{book_id}/reject", response_model=StatusResponse)
async def reject_reservation(
    book_id: int = FPath(..., gt=0),
    payload: RejectRequest = Body(...),
    service: LibraryService = Depends(get_service),
):
    await service.reject_reservation(book_id, payload.admin_id, payload.reason)
    return {"status": "rejected"}


@protected.post("/books/{book_id}/return", response_model=StatusResponse)
async def return_book_endpoint(
    book_id:  int = FPath(..., gt=0),
    user_id:  int           = Form(..., gt=0),
    photo: Optional[UploadFile] = File(None),
    service: LibraryService = Depends(get_service),
):
    return await service.return_book(book_id, user_id, photo)


@protected.post("/books/{book_id}/waitlist", response_model=WaitlistResponse)
async def join_waitlist(
    book_id: int = FPath(..., gt=0),
    payload: WaitlistRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.is_deleted:
        raise HTTPException(status_code=404, detail="Книга не найдена")

    if book.status == BookStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="Книга доступна, можно брать")

    if book.owner_id == payload.user_id:
        raise HTTPException(status_code=400, detail="Нельзя встать в очередь на свою книгу")

    if book.borrower_id == payload.user_id:
        if book.status == BookStatus.RESERVED:
            raise HTTPException(status_code=400, detail="Вы уже забронировали эту книгу")
        raise HTTPException(status_code=400, detail="Вы уже держите эту книгу")

    added = await repo.add_to_waitlist(book_id, payload.user_id)

    if added:
        return {"message": "Вы добавлены в лист ожидания", "added": True}
    return {"message": "Вы уже в листе ожидания", "added": False}


@protected.delete("/books/{book_id}/waitlist", response_model=WaitlistResponse)
async def leave_waitlist(
    book_id: int = FPath(..., gt=0),
    user_id: int = Query(..., gt=0, description="Telegram ID пользователя, покидающего очередь"),
    db: AsyncSession = Depends(get_db),
):
    """
    Пользователь отписывается от уведомлений по книге.
    Идемпотентный DELETE: если пользователь не был в очереди — всё равно 200 OK.
    Намеренно НЕ проверяет is_deleted — пользователь должен иметь возможность
    покинуть очередь даже удалённой книги (cleanup).
    """
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")

    await repo.remove_from_waitlist(book_id, user_id)
    return {"message": "Вы удалены из листа ожидания"}


# ==========================================================================
# BOT WEBHOOK
# ==========================================================================

@protected.post("/bot/webhook", response_model=StatusResponse)
async def bot_webhook(payload: NotificationPayload):
    """
    Приём входящих вебхуков от бота (reserved for future use).
    В текущей архитектуре API сам инициирует push к боту,
    но этот эндпоинт оставлен для двунаправленного взаимодействия.
    """
    logger.info("Webhook received: %s for user %s", payload.type, payload.user_id)
    return {"status": "received"}


# ==========================================================================
# ADMIN PANEL
# ==========================================================================

class PendingReservationsRequest(BaseModel):
    """
    requester_id передаётся в теле POST — не в query param.
    Telegram ID администратора не должен попадать в access-логи nginx/прокси.
    """
    requester_id: int = Field(..., gt=0, description="Telegram ID администратора")


@protected.post("/admin/pending-reservations", response_model=Page[BookRead])
async def get_pending_reservations(
    payload: PendingReservationsRequest = Body(...),
    limit:  int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    user_repo = UserRepository(db)
    if not await user_repo.is_admin(payload.requester_id):
        raise HTTPException(
            status_code=403,
            detail="Только администратор может видеть список ожидающих резерваций",
        )
    book_repo = BookRepository(db)
    books = await book_repo.get_books(status=BookStatus.RESERVED, limit=limit, offset=offset)
    total = await book_repo.count_books(status=BookStatus.RESERVED)
    items = [_enrich_book_dto(BookRead.model_validate(b), b) for b in books]
    return Page(items=items, total=total, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Подключаем роутеры
# ---------------------------------------------------------------------------
app.include_router(protected)
app.include_router(notification_router, dependencies=[Depends(verify_bot_token)])