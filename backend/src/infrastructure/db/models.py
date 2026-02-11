from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
from domain.domain_models import BookStatus

Base = declarative_base()


class UserTable(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)  # Telegram ID
    full_name = Column(String, nullable=False)
    username = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BookTable(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    author = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    genre = Column(String(50), nullable=True)
    isbn = Column(String(20), nullable=True)

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    owner = relationship("UserTable", foreign_keys=[owner_id], backref="owned_books")

    status = Column(SAEnum(BookStatus), default=BookStatus.AVAILABLE)
    image_path = Column(String, nullable=True)

    # Логика выдачи
    borrower_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    borrower = relationship("UserTable", foreign_keys=[borrower_id], backref="borrowed_books")

    return_due_date = Column(DateTime, nullable=True)

    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BookHistoryTable(Base):
    __tablename__ = "book_history"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # Кто совершил действие
    status_to = Column(SAEnum(BookStatus), nullable=False)
    comment = Column(String, nullable=True)
    photo_proof_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WaitlistTable(Base):
    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)