from enum import Enum
from dataclasses import dataclass
from typing import Optional


class BookStatus(str, Enum):
    AVAILABLE = "available"   # 🟢 Свободна
    RESERVED  = "reserved"    # 🟡 Забронирована (ждёт подтверждения админа)
    BORROWED  = "borrowed"    # 🔴 Выдана
    OVERDUE   = "overdue"     # ⏳ Просрочена (ставится фоновой задачей)
    # FIX #14: добавлен статус для корректной записи в историю при удалении книги
    DELETED   = "deleted"     # 🗑️  Удалена (soft-delete)


class NotificationType(str, Enum):
    BOOK_RETURNED         = "book_returned"
    WAITLIST_AVAILABLE    = "waitlist_available"
    RESERVATION_APPROVED  = "reservation_approved"
    RESERVATION_REJECTED  = "reservation_rejected"
    OVERDUE               = "overdue"
    NEW_BOOK              = "new_book"


@dataclass
class Book:
    id: Optional[int]
    title: str
    author: str
    owner_id: int
    status: BookStatus = BookStatus.AVAILABLE
    image_path: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    isbn: Optional[str] = None
    is_deleted: bool = False