from enum import Enum
from dataclasses import dataclass
from typing import Optional

class BookStatus(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    BORROWED = "borrowed"

@dataclass
class Book:
    id: Optional[int]
    title: str
    author: str
    owner_id: int  # Telegram ID владельца
    status: BookStatus = BookStatus.AVAILABLE
    image_path: Optional[str] = None