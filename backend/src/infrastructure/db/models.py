from datetime import datetime
from typing import Optional

from sqlalchemy import String, BigInteger, Enum as SQLEnum, ForeignKey, DateTime, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from domain.models import BookStatus

class Base(DeclarativeBase):
    pass

class UserTable(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))

class BookTable(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    author: Mapped[str] = mapped_column(String(255), index=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[BookStatus] = mapped_column(
        SQLEnum(BookStatus), default=BookStatus.AVAILABLE, index=True
    )

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    borrower_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))

    # Сроки
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    return_due_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    image_path: Mapped[Optional[str]] = mapped_column(String(512))


class BookHistoryTable(Base):
    """Таблица для журнала событий (Пункт 4.4 ТЗ)"""
    __tablename__ = "book_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # Кто совершил действие
    status_to: Mapped[BookStatus] = mapped_column(SQLEnum(BookStatus))
    comment: Mapped[Optional[str]] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())