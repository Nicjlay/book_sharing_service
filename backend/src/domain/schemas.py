from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from .domain_models import BookStatus, NotificationType


# ---------------------------------------------------------------------------
# Общие части
# ---------------------------------------------------------------------------

class BookBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field(..., min_length=3, max_length=100)
    description: Optional[str] = None
    genre: Optional[str] = "Другое"
    isbn: Optional[str] = None
    image_path: Optional[str] = None

    @validator("author")
    def validate_author(cls, v):
        parts = v.strip().split()
        if len(parts) < 2:
            raise ValueError("Введите полное имя и фамилию автора")
        return v.title()


# ---------------------------------------------------------------------------
# Создание (POST)
# ---------------------------------------------------------------------------

class BookCreate(BookBase):
    owner_id: int


# ---------------------------------------------------------------------------
# Редактирование (PATCH)
# ---------------------------------------------------------------------------

class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    image_path: Optional[str] = None
    # FIX #22: isbn отсутствовал в BookUpdate — нельзя было исправить ISBN после создания.
    isbn: Optional[str] = None

    @validator("author")
    def validate_author_update(cls, v):
        if v:
            parts = v.strip().split()
            if len(parts) < 2:
                raise ValueError("Введите полное имя и фамилию автора")
            return v.title()
        return v


# ---------------------------------------------------------------------------
# Чтение (GET)
# ---------------------------------------------------------------------------

class BookRead(BookBase):
    id: int
    status: BookStatus
    owner_id: int
    borrower_id: Optional[int] = None
    return_due_date: Optional[datetime] = None
    is_deleted: bool
    created_at: datetime

    # Денормализованные поля для UI (заполняются в эндпоинтах через _enrich_book_dto)
    owner_username: Optional[str] = None
    owner_full_name: Optional[str] = None
    # owner_tg_id == owner_id (Telegram ID хранится как PK); оставлен для обратной совместимости
    owner_tg_id: Optional[int] = None
    borrower_username: Optional[str] = None
    borrower_full_name: Optional[str] = None
    borrower_tg_id: Optional[int] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------

class UserRead(BaseModel):
    # users.id == Telegram ID (BigInteger PK)
    id: int
    full_name: str
    username: Optional[str] = None
    is_admin: bool

    class Config:
        from_attributes = True


class UserAuthRequest(BaseModel):
    tg_id: int
    full_name: str
    username: Optional[str] = None
    # FIX #2, #20: is_admin УДАЛЁН из клиентского запроса.
    # Права администратора управляются только через БД / отдельный защищённый эндпоинт.
    # get_or_create_user НЕ перезаписывает is_admin для существующих пользователей.


# ---------------------------------------------------------------------------
# Жанры
# ---------------------------------------------------------------------------

class GenreList(BaseModel):
    genres: List[str]


# ---------------------------------------------------------------------------
# История
# ---------------------------------------------------------------------------

class BookHistoryRead(BaseModel):
    id: int
    book_id: int
    action_date: datetime
    user_id: int
    status_to: BookStatus
    comment: Optional[str]
    photo_proof_path: Optional[str]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Запросы действий
# ---------------------------------------------------------------------------

class ReservationRequest(BaseModel):
    user_id: int
    # FIX #12: добавлена валидация — нельзя бронировать на 0 или 100+ дней.
    days: int = Field(default=14, ge=1, le=90)


class ApproveRequest(BaseModel):
    admin_id: int
    due_date: datetime

    # FIX #13: due_date не может быть в прошлом — иначе книга сразу попадёт в OVERDUE.
    @validator("due_date")
    def due_date_must_be_future(cls, v):
        if v <= datetime.now():
            raise ValueError("due_date должна быть в будущем")
        return v


class RejectRequest(BaseModel):
    admin_id: int
    reason: str = "Отклонено администратором"


# FIX #23: user_id для вступления в waitlist вынесен в тело запроса
# (раньше был query-параметром POST-запроса и попадал в access-логи).
class WaitlistRequest(BaseModel):
    user_id: int


# ---------------------------------------------------------------------------
# Webhook Payload (Сервер → Бот)
# ---------------------------------------------------------------------------

class NotificationPayload(BaseModel):
    """Формат данных, которые сервер отправляет боту по HTTP"""
    user_id: int          # -1 = всем админам, 0 = в группу
    type: NotificationType
    message: str
    book_id: Optional[int] = None
    meta: dict = {}