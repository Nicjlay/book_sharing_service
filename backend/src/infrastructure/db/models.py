from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text,
    Enum as SAEnum, BigInteger, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
from domain.domain_models import BookStatus

Base = declarative_base()


class UserTable(Base):
    __tablename__ = "users"

    id         = Column(BigInteger, primary_key=True, index=True)  # Telegram ID как PK
    full_name  = Column(String, nullable=False)
    username   = Column(String, nullable=True, index=True)
    is_admin   = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def tg_id(self) -> int:
        """Алиас для единообразия: book.owner.tg_id везде работает без AttributeError."""
        return self.id


class BookTable(Base):
    __tablename__ = "books"

    id          = Column(Integer, primary_key=True, index=True)
    title       = Column(String(200), nullable=False, index=True)
    author      = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    genre       = Column(String(50), nullable=True, index=True)
    isbn        = Column(String(20), nullable=True)

    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    owner    = relationship("UserTable", foreign_keys=[owner_id], backref="owned_books")

    status     = Column(SAEnum(BookStatus), default=BookStatus.AVAILABLE, index=True)
    image_path = Column(String, nullable=True)

    borrower_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    borrower    = relationship("UserTable", foreign_keys=[borrower_id], backref="borrowed_books")

    return_due_date = Column(DateTime, nullable=True, index=True)
    is_deleted      = Column(Boolean, default=False, index=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    # FIX #19: составной индекс для запроса фоновой задачи:
    #   WHERE status = 'borrowed' AND return_due_date < NOW()
    # Без него PostgreSQL делает seq-scan всей таблицы каждый час.
    # ВАЖНО: не забудьте создать соответствующую Alembic-миграцию!
    __table_args__ = (
        Index("ix_books_status_due_date", "status", "return_due_date"),
    )


class BookHistoryTable(Base):
    __tablename__ = "book_history"

    id               = Column(Integer, primary_key=True, index=True)
    book_id          = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)
    user_id          = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    status_to        = Column(SAEnum(BookStatus), nullable=False)
    comment          = Column(String, nullable=True)
    photo_proof_path = Column(String, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def action_date(self):
        return self.created_at


class WaitlistTable(Base):
    __tablename__ = "waitlist"

    id         = Column(Integer, primary_key=True, index=True)
    book_id    = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)
    user_id    = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # Гарантирует: один пользователь не попадёт в очередь одной книги дважды,
        # даже при конкурентных запросах (ON CONFLICT DO NOTHING использует это имя).
        UniqueConstraint("book_id", "user_id", name="uq_waitlist_book_user"),
    )