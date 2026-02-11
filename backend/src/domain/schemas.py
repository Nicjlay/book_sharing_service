from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from .domain_models import BookStatus, NotificationType


# --- Общие части ---

class BookBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field(..., min_length=3, max_length=100)
    description: Optional[str] = None
    genre: Optional[str] = "Другое"
    isbn: Optional[str] = None
    image_path: Optional[str] = None

    @validator('author')
    def validate_author(cls, v):
        # Валидация на стороне сервера (страховка).
        # Основную проверку UI делает бот, но БД не должна принимать мусор.
        parts = v.strip().split()
        if len(parts) < 2:
            raise ValueError("Введите полное имя и фамилию автора")
        return v.title()


# --- Создание (POST) ---
class BookCreate(BookBase):
    owner_id: int


# --- Редактирование (PATCH) ---
class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    image_path: Optional[str] = None

    @validator('author')
    def validate_author_update(cls, v):
        if v:
            parts = v.strip().split()
            if len(parts) < 2:
                raise ValueError("Введите полное имя и фамилию автора")
            return v.title()
        return v


# --- Чтение (GET) ---
class BookRead(BookBase):
    id: int
    status: BookStatus
    owner_id: int
    borrower_id: Optional[int] = None
    return_due_date: Optional[datetime] = None
    is_deleted: bool
    created_at: datetime

    # Дополнительные поля для UI
    owner_username: Optional[str] = None
    owner_full_name: Optional[str] = None

    class Config:
        from_attributes = True


# --- Пользователи ---
class UserRead(BaseModel):
    id: int
    full_name: str
    username: Optional[str] = None
    is_admin: bool

    class Config:
        from_attributes = True


# --- Жанры ---
class GenreList(BaseModel):
    genres: List[str]


# --- История ---
class BookHistoryRead(BaseModel):
    id: int
    action_date: datetime
    user_id: int
    status_to: BookStatus
    comment: Optional[str]
    photo_proof_path: Optional[str]

    class Config:
        from_attributes = True


# --- Запросы действий ---
class ReservationRequest(BaseModel):
    user_id: int
    days: int = 14


class ApproveRequest(BaseModel):
    admin_id: int
    due_date: datetime


class RejectRequest(BaseModel):
    admin_id: int
    reason: str = "Отклонено администратором"


# --- Webhook Payload (Сервер -> Бот) ---
class NotificationPayload(BaseModel):
    """Формат данных, которые сервер отправляет боту по HTTP"""
    user_id: int
    type: NotificationType
    message: str
    book_id: Optional[int] = None
    meta: dict = {}