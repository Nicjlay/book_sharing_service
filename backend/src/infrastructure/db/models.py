from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum, BigInteger, UniqueConstraint
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
from domain.domain_models import BookStatus

Base = declarative_base()


class UserTable(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)  # Telegram ID (используется как PK)
    full_name = Column(String, nullable=False)
    username = Column(String, nullable=True, index=True)
    is_admin = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def tg_id(self) -> int:
        """
        Telegram ID пользователя.
        В данной схеме id и есть tg_id — хранится как первичный ключ.
        Свойство добавлено для единообразия кода: book.owner.tg_id
        работает везде без AttributeError.
        """
        return self.id


class BookTable(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False, index=True)
    author = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    genre = Column(String(50), nullable=True, index=True)
    isbn = Column(String(20), nullable=True)

    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    owner = relationship("UserTable", foreign_keys=[owner_id], backref="owned_books")

    status = Column(SAEnum(BookStatus), default=BookStatus.AVAILABLE, index=True)
    image_path = Column(String, nullable=True)

    borrower_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    borrower = relationship("UserTable", foreign_keys=[borrower_id], backref="borrowed_books")

    return_due_date = Column(DateTime, nullable=True, index=True)

    is_deleted = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BookHistoryTable(Base):
    __tablename__ = "book_history"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    status_to = Column(SAEnum(BookStatus), nullable=False)
    comment = Column(String, nullable=True)
    photo_proof_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def action_date(self):
        return self.created_at


class WaitlistTable(Base):
    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Fix #8: был комментарий об уникальности, но никакого реального ограничения
    # не было. Теперь БД сама гарантирует, что один пользователь не попадёт
    # в очередь одной книги дважды, даже при конкурентных запросах.
    __table_args__ = (
        UniqueConstraint("book_id", "user_id", name="uq_waitlist_book_user"),
    )