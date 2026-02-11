from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime
from .domain_models import BookStatus


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
        # ТЗ: "Введите фамилию и имя... если одно слово - уточните"
        # API не может "уточнить", но может отклонить, чтобы бот перезапросил
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

    # Для отображения в каталоге
    owner_username: Optional[str] = None

    class Config:
        from_attributes = True


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
    admin_id: int  # Кто подтверждает
    due_date: datetime


class RejectRequest(BaseModel):
    admin_id: int
    reason: str = "Отклонено администратором"


class ReturnRequest(BaseModel):
    user_id: int  # Кто возвращает (может быть админ или заемщик)
    is_admin: bool = False  # Флаг, если возвращает админ принудительно