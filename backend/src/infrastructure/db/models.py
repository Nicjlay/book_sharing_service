from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text,
    Enum as SAEnum, BigInteger, UniqueConstraint, Index,
    func, text,
)
from sqlalchemy.orm import relationship, declarative_base
from domain.domain_models import BookStatus

Base = declarative_base()


class UserTable(Base):
    __tablename__ = "users"

    id         = Column(BigInteger, primary_key=True, index=True)  # Telegram ID как PK
    full_name  = Column(String(255), nullable=False)
    username   = Column(String(32), nullable=True, index=True)
    is_admin   = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @property
    def tg_id(self) -> int:
        """Алиас для единообразия: book.owner.tg_id везде работает без AttributeError."""
        return self.id


# NOTE: для поиска по username/full_name через ILIKE('%...%') необходим GIN-индекс
# на расширении pg_trgm. Добавить в отдельной Alembic-миграции:
#
#   op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
#   op.execute("""
#       CREATE INDEX ix_users_username_trgm
#       ON users USING GIN (username gin_trgm_ops)
#   """)
#   op.execute("""
#       CREATE INDEX ix_users_full_name_trgm
#       ON users USING GIN (full_name gin_trgm_ops)
#   """)
#
# Без этих индексов поиск при > ~10 000 пользователей становится full-scan.


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
    image_path = Column(String(200), nullable=True)

    borrower_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    borrower    = relationship("UserTable", foreign_keys=[borrower_id], backref="borrowed_books")

    return_due_date = Column(DateTime(timezone=True), nullable=True, index=True)
    is_deleted      = Column(Boolean, default=False, index=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Составной индекс для фоновой задачи:
    #   WHERE status = 'borrowed' AND return_due_date < NOW()
    # Без него PostgreSQL делает seq-scan всей таблицы каждый час.
    __table_args__ = (
        Index("ix_books_status_due_date", "status", "return_due_date"),
    )


class BookHistoryTable(Base):
    __tablename__ = "book_history"

    id               = Column(Integer, primary_key=True, index=True)
    book_id          = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)
    user_id          = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    status_to        = Column(SAEnum(BookStatus), nullable=False)
    comment          = Column(String(500), nullable=True)
    photo_proof_path = Column(String(200), nullable=True)
    created_at       = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Явный DESC для created_at в составном индексе.
    #
    # get_history выполняет:
    #   SELECT ... WHERE book_id = :id ORDER BY created_at DESC LIMIT :lim OFFSET :off
    #
    # Без DESC PostgreSQL использует backward index scan — менее эффективен при LIMIT.
    # Явный DESC гарантирует forward scan для самого частого запроса.
    #
    # Alembic-миграция:
    #   op.create_index(
    #       "ix_book_history_book_id_created_at",
    #       "book_history",
    #       ["book_id", sa.text("created_at DESC")],
    #   )
    __table_args__ = (
        Index(
            "ix_book_history_book_id_created_at",
            "book_id",
            text("created_at DESC"),
        ),
    )

    @property
    def action_date(self):
        return self.created_at


class WaitlistTable(Base):
    __tablename__ = "waitlist"

    id         = Column(Integer, primary_key=True, index=True)
    book_id    = Column(Integer, ForeignKey("books.id"), nullable=False)
    user_id    = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Уникальность: один пользователь не попадёт в очередь одной книги дважды
        # (ON CONFLICT DO NOTHING в add_to_waitlist использует это имя).
        UniqueConstraint("book_id", "user_id", name="uq_waitlist_book_user"),

        # Составной индекс (book_id, created_at ASC).
        #
        # pop_first_waiter выполняет:
        #   SELECT id FROM waitlist WHERE book_id = :id ORDER BY created_at LIMIT 1
        #
        # get_waitlist_users выполняет:
        #   SELECT user_id FROM waitlist WHERE book_id = :id ORDER BY created_at
        #
        # С составным индексом (book_id, created_at) обе запросы — index-only scan.
        # ORDER BY created_at уже упорядочен индексом → LIMIT 1 = O(1).
        Index("ix_waitlist_book_created", "book_id", "created_at"),
    )
