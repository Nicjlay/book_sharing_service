from pydantic import BaseModel, Field
from typing import Optional
from .models import BookStatus

# То, что мы ждем от фронтенда/бота при создании книги
class BookCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field(..., min_length=1, max_length=100)
    owner_id: int
    image_path: Optional[str] = None

# То, что мы отдаем клиенту (скрываем лишнее, если нужно)
class BookRead(BaseModel):
    id: int
    title: str
    author: str
    status: BookStatus
    owner_id: int
    image_path: Optional[str] = None

    class Config:
        from_attributes = True # Позволяет Pydantic читать данные прямо из SQLAlchemy моделей