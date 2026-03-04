import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Any, Dict, Generic, TypeVar
from datetime import datetime, timezone, timedelta

from domain.domain_models import BookStatus, NotificationType


_SAFE_IMAGE_PATH_RE = re.compile(r"^[\w\-/]+\.\w{1,10}$")
_ISBN_RE = re.compile(r"^(?:\d[\- ]?){9}[\dXx]$|^(?:\d[\- ]?){12}\d$")
_MAX_DUE_DATE_DELTA = timedelta(days=730)
USERS_QUERY_MIN_LENGTH = 2


def _validate_image_path(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    normalized = v.replace("\\", "/")
    if ".." in normalized or normalized.startswith("/"):
        raise ValueError("Недопустимый путь к изображению")
    if not _SAFE_IMAGE_PATH_RE.match(normalized):
        raise ValueError(
            "image_path должен иметь вид 'books/<имя файла>.<расширение>'"
        )
    return normalized


def _normalize_author(v: str) -> str:
    v = v.strip()
    v = re.sub(r"\s+", " ", v)
    if len(v) < 2:
        raise ValueError("Имя автора слишком короткое (минимум 2 символа)")
    return v


def _validate_isbn(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    if not _ISBN_RE.match(v):
        raise ValueError(
            "ISBN должен содержать 10 или 13 цифр "
            "(дефисы и пробелы допускаются как разделители)"
        )
    return v


def _normalize_genre(v: Optional[str]) -> str:
    """Для CREATE-операций: None → 'Другое', пустая строка → 'Другое'."""
    if v is None:
        return "Другое"
    v = v.strip()
    return v if v else "Другое"


def _normalize_genre_update(v: Optional[str]) -> Optional[str]:
    """
    Для PATCH-операций: None означает «очистить поле», не «использовать default».

    Различие важно для семантики PATCH:
    - поле отсутствует в теле → не попадает в model_fields_set → не изменяется
    - поле явно null           → попадает в model_fields_set как None → обнуляется в БД
    - поле = строка            → нормализуется как обычно
    """
    if v is None:
        return None
    v = v.strip()
    return v if v else "Другое"


class BookBase(BaseModel):
    title:       str           = Field(..., min_length=1, max_length=200)
    author:      str           = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    genre:       Optional[str] = Field("Другое", max_length=50)
    isbn:        Optional[str] = Field(None, max_length=20)
    image_path:  Optional[str] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
            v = re.sub(r"\s+", " ", v)
        return v

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @field_validator("author", mode="before")
    @classmethod
    def validate_author(cls, v: str) -> str:
        return _normalize_author(v)

    @field_validator("genre", mode="before")
    @classmethod
    def normalize_genre(cls, v: Optional[str]) -> str:
        return _normalize_genre(v)

    @field_validator("isbn", mode="after")
    @classmethod
    def validate_isbn(cls, v: Optional[str]) -> Optional[str]:
        return _validate_isbn(v)

    @field_validator("image_path", mode="after")
    @classmethod
    def validate_image_path(cls, v: Optional[str]) -> Optional[str]:
        return _validate_image_path(v)


class BookCreate(BookBase):
    owner_id: int = Field(..., gt=0, description="Telegram ID владельца (всегда > 0)")


class BookUpdate(BaseModel):
    title:       Optional[str] = Field(None, min_length=1, max_length=200)
    author:      Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    genre:       Optional[str] = Field(None, max_length=50)
    image_path:  Optional[str] = None
    isbn:        Optional[str] = Field(None, max_length=20)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title_update(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            v = re.sub(r"\s+", " ", v)
        return v

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description_update(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @field_validator("author", mode="before")
    @classmethod
    def validate_author_update(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _normalize_author(v)
        return v

    @field_validator("genre", mode="before")
    @classmethod
    def normalize_genre_update(cls, v: Optional[str]) -> Optional[str]:
        # FIX: PATCH-семантика: None остаётся None (= обнуление поля в БД),
        # а не преобразуется в "Другое". Это отличается от BookCreate/BookBase,
        # где None → "Другое" (при создании жанр всегда должен быть задан).
        return _normalize_genre_update(v)

    @field_validator("isbn", mode="after")
    @classmethod
    def validate_isbn(cls, v: Optional[str]) -> Optional[str]:
        return _validate_isbn(v)

    @field_validator("image_path", mode="after")
    @classmethod
    def validate_image_path(cls, v: Optional[str]) -> Optional[str]:
        return _validate_image_path(v)


class BookRead(BookBase):
    """
    DTO для чтения книги из БД.

    FIX: Переопределяем валидаторы genre и image_path из BookBase, чтобы они
    НЕ применяли нормализацию при чтении.

    Причина:
    - BookBase.normalize_genre преобразует None → "Другое" — правильно для
      операций CREATE, но неприемлемо для READ: NULL в БД должен остаться null
      в ответе API, иначе клиент не может отличить "жанр не задан" от
      "жанр явно установлен как Другое".
    - Аналогично для image_path: путь уже провалидирован при записи;
      повторная валидация при чтении может отклонить легитимные значения,
      если в будущем изменится регулярное выражение.
    """

    id:              int
    status:          BookStatus
    owner_id:        int
    borrower_id:     Optional[int]      = None
    return_due_date: Optional[datetime] = None
    is_deleted:      bool
    created_at:      datetime

    owner_username:     Optional[str] = None
    owner_full_name:    Optional[str] = None
    owner_tg_id:        Optional[int] = None
    borrower_username:  Optional[str] = None
    borrower_full_name: Optional[str] = None
    borrower_tg_id:     Optional[int] = None

    model_config = {"from_attributes": True}

    # FIX: При чтении из БД жанр сохраняется как есть.
    # Если в БД хранится NULL — API возвращает null (не "Другое").
    @field_validator("genre", mode="before")
    @classmethod
    def normalize_genre(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
        return v

    # FIX: image_path тоже не нормализуем при чтении — значение уже
    # было провалидировано при записи и изменять его не нужно.
    @field_validator("image_path", mode="after")
    @classmethod
    def validate_image_path(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
        return v


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """
    Стандартный пагинированный ответ.

    total = -1 означает «total неизвестен» (fuzzy-поиск не поддерживает COUNT).
    Клиент может использовать has_more = len(items) == limit как альтернативу.
    """
    items:  List[T]
    total:  int
    limit:  int
    offset: int


class UserRead(BaseModel):
    id:         int
    full_name:  str
    username:   Optional[str] = None
    is_admin:   bool
    created_at: datetime
    tg_id:      int = Field(description="Алиас для id — Telegram ID пользователя")

    model_config = {"from_attributes": True}


class UserAuthRequest(BaseModel):
    tg_id:     int           = Field(..., gt=0, description="Telegram ID пользователя (всегда > 0)")
    full_name: str           = Field(..., min_length=1, max_length=255)
    username:  Optional[str] = Field(None, max_length=32)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


class GenreList(BaseModel):
    genres: List[str]


class BookHistoryRead(BaseModel):
    id:               int
    book_id:          int
    action_date:      datetime
    user_id:          int
    status_to:        BookStatus
    comment:          Optional[str]
    photo_proof_path: Optional[str]

    model_config = {"from_attributes": True}


class ReservationRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    days:    int = Field(default=14, ge=1, le=90)


class ApproveRequest(BaseModel):
    admin_id: int      = Field(..., gt=0)
    due_date: datetime

    @field_validator("due_date", mode="after")
    @classmethod
    def due_date_must_be_future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        if v <= now:
            raise ValueError("due_date должна быть в будущем")

        if v > now + _MAX_DUE_DATE_DELTA:
            raise ValueError(
                f"due_date не может быть больше чем через {_MAX_DUE_DATE_DELTA.days} дней"
            )

        return v


class RejectRequest(BaseModel):
    admin_id: int = Field(..., gt=0)
    reason:   str = Field(
        default="Отклонено администратором",
        max_length=500,
        description="Причина отказа (до 500 символов)",
    )


class WaitlistRequest(BaseModel):
    user_id: int = Field(..., gt=0)


class SetAdminRequest(BaseModel):
    is_admin:     bool
    requester_id: int = Field(..., gt=0, description="Telegram ID того, кто выдаёт права")


class DeleteBookRequest(BaseModel):
    """
    user_id перенесён в query-параметр эндпоинта DELETE /books/{book_id}.

    Этот класс оставлен для документирования принятого решения, не используется
    в эндпоинте напрямую.

    Причина отказа от тела в DELETE:
    - HTTP/1.1 RFC 7231 §4.3.5 допускает тело у DELETE, но не определяет его семантику.
    - nginx, AWS ALB и ряд CDN стрипают тело DELETE-запросов → 422 на сервере.
    - httpx и некоторые версии fetch игнорируют body у DELETE.
    """
    pass


class StatusResponse(BaseModel):
    status: str


class WaitlistResponse(BaseModel):
    message: str
    added:   Optional[bool] = None


class NotificationPayload(BaseModel):
    """
    Формат данных, которые сервер отправляет боту по HTTP.

    Семантика user_id:
      user_id > 0  — конкретный Telegram-пользователь
      user_id == 0 — широковещательная рассылка в группу
      user_id == -1 — рассылка всем администраторам
    """
    user_id: int               = Field(..., ge=-1, description="-1=all admins, 0=group, >0=user tg_id")
    type:    NotificationType
    message: str               = Field(..., max_length=4096)
    # FIX: book_id должен быть положительным если указан (ID книги не может быть 0 или отрицательным).
    book_id: Optional[int]     = Field(default=None, gt=0)
    meta:    Dict[str, Any]    = Field(default_factory=dict)
