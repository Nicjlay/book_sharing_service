from typing import Optional

from sqlalchemy import String, BigInteger, Enum as SQLEnum, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from domain.models import BookStatus

class Base(DeclarativeBase):
    pass

class UserTable(Base):
    __tablename__ = "users"

    # Используем Telegram ID как первичный ключ, т.к. он уникален
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(32))
    full_name: Mapped[str] = mapped_column(String(255))

class BookTable(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    author: Mapped[str] = mapped_column(String(255))
    status: Mapped[BookStatus] = mapped_column(
        SQLEnum(BookStatus), default=BookStatus.AVAILABLE
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    image_path: Mapped[Optional[str]] = mapped_column(String(512))