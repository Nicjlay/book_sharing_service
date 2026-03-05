"""
Репозитории для работы с БД.

Принцип разграничения транзакций (Unit of Work):
- Методы, вызываемые из LibraryService, НЕ коммитят самостоятельно.
  Сервисный метод выполняет все операции и вызывает commit() один раз в конце.
  Это гарантирует атомарность составных операций (update_status + log_history и т.д.).
- Методы UserRepository и методы waitlist коммитят самостоятельно,
  так как вызываются напрямую из эндпоинтов без сервисного слоя.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, update, delete, and_, desc, distinct, or_, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.models import UserTable, BookTable, BookHistoryTable, WaitlistTable
from domain.domain_models import BookStatus
from domain.schemas import BookCreate, BookUpdate

logger = logging.getLogger(__name__)


def _escape_like(value: str) -> str:
    """
    Экранирует специальные символы LIKE-паттерна: % _ \\
    Без экранирования запрос вида q='%' матчит всех пользователей подряд,
    а q='_' матчит всех у кого username длиной ≥ 1 символа (= все).
    """
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", r"\%")
        .replace("_", r"\_")
    )


# ---------------------------------------------------------------------------
# Кэш жанров
#
# get_all_genres кэшируется на 60 секунд.
# asyncio.Lock гарантирует что только один корутин обновляет кэш (thundering herd).
# invalidate_genres_cache() сбрасывает кэш при изменении жанрового состава.
# ---------------------------------------------------------------------------
_genres_cache:    Optional[List[str]] = None
_genres_cache_at: float               = 0.0
_GENRES_CACHE_TTL: float              = 60.0  # секунды
_genres_refresh_lock: Optional[asyncio.Lock] = None


def _get_genres_lock() -> asyncio.Lock:
    """Ленивая инициализация Lock в работающем event loop."""
    global _genres_refresh_lock
    if _genres_refresh_lock is None:
        _genres_refresh_lock = asyncio.Lock()
    return _genres_refresh_lock


def invalidate_genres_cache() -> None:
    """
    Немедленно сбрасывает кэш жанров.

    Вызывать после операций, которые могут изменить набор жанров:
    - add_book (новый жанр → появляется в списке)
    - soft_delete_book (удаление последней книги жанра → жанр должен исчезнуть)
    """
    global _genres_cache, _genres_cache_at
    _genres_cache    = None
    _genres_cache_at = 0.0


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(
        self,
        tg_id: int,
        full_name: str,
        username: str = None,
    ) -> UserTable:
        """
        Создаёт нового пользователя или обновляет имя/username существующего.
        Атомарный PostgreSQL INSERT ... ON CONFLICT DO UPDATE.
        Коммитит самостоятельно (вызывается напрямую из эндпоинта).
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(UserTable)
            .values(
                id=tg_id,
                full_name=full_name,
                username=username,
                is_admin=False,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "full_name": full_name,
                    "username":  username,
                    # is_admin — намеренно не трогаем
                },
            )
        )
        await self.session.execute(stmt)
        await self.session.commit()

        user = await self.session.get(UserTable, tg_id)
        if user is not None:
            await self.session.refresh(user)
        return user

    async def set_admin(self, tg_id: int, is_admin: bool) -> Optional[UserTable]:
        """
        Явное управление флагом администратора.
        Коммитит самостоятельно (вызывается напрямую из эндпоинта).
        """
        stmt = (
            update(UserTable)
            .where(UserTable.id == tg_id)
            .values(is_admin=is_admin)
            .returning(UserTable.id)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        if result.scalar() is None:
            return None
        user = await self.session.get(UserTable, tg_id)
        if user is not None:
            await self.session.refresh(user)
        return user

    async def get_by_id(self, user_id: int) -> Optional[UserTable]:
        return await self.session.get(UserTable, user_id)

    async def is_admin(self, user_id: int) -> bool:
        result = await self.session.execute(
            select(UserTable.is_admin).where(UserTable.id == user_id)
        )
        flag = result.scalar_one_or_none()
        return bool(flag)

    async def exists(self, user_id: int) -> bool:
        """
        Проверяет существование пользователя без загрузки всего объекта.
        Используется перед отправкой уведомлений из листа ожидания,
        чтобы не слать push на несуществующих (удалённых) пользователей.
        """
        result = await self.session.execute(
            select(UserTable.id).where(UserTable.id == user_id)
        )
        return result.scalar_one_or_none() is not None

    async def search_users(
        self,
        query_str: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[UserTable]:
        """
        Поиск пользователей с поддержкой пагинации.
        """
        stmt = select(UserTable)
        if query_str:
            escaped = _escape_like(query_str)
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    UserTable.username.ilike(pattern, escape="\\"),
                    UserTable.full_name.ilike(pattern, escape="\\"),
                )
            )
        stmt = stmt.order_by(UserTable.full_name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_users(self, query_str: str = None) -> int:
        """
        Подсчёт числа пользователей с теми же фильтрами что у search_users.
        """
        stmt = select(func.count()).select_from(UserTable)
        if query_str:
            escaped = _escape_like(query_str)
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    UserTable.username.ilike(pattern, escape="\\"),
                    UserTable.full_name.ilike(pattern, escape="\\"),
                )
            )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_all_admins(self) -> List[UserTable]:
        stmt   = select(UserTable).where(UserTable.is_admin.is_(True))
        result = await self.session.execute(stmt)
        return result.scalars().all()


class BookRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -----------------------------------------------------------------------
    # Создание / Изменение / Удаление
    # Эти методы НЕ коммитят — commit() выполняет сервисный метод.
    # -----------------------------------------------------------------------

    async def add_book(self, book_data: BookCreate, image_path: str | None = None) -> BookTable:
        """
        image_path — финальный путь к изображению.
        Передаётся явно, а не через мутацию book_data
        (book_data — неизменяемый входной объект).

        Инвалидирует кэш жанров при создании книги.
        """
        new_book = BookTable(
            title=book_data.title,
            author=book_data.author,
            owner_id=book_data.owner_id,
            description=book_data.description,
            genre=book_data.genre,
            isbn=book_data.isbn,
            image_path=image_path or book_data.image_path,
            status=BookStatus.AVAILABLE,
        )
        self.session.add(new_book)
        await self.session.flush()
        invalidate_genres_cache()
        return new_book

    async def get_book_by_id(self, book_id: int) -> Optional[BookTable]:
        query = (
            select(BookTable)
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower),
            )
            .where(BookTable.id == book_id)
        )
        result = await self.session.execute(query)
        return result.scalars().one_or_none()

    async def get_books_by_ids(self, book_ids: List[int]) -> List[BookTable]:
        """
        Пакетная загрузка книг по списку ID — один запрос вместо N.
        Порядок результатов совпадает с порядком book_ids.
        """
        if not book_ids:
            return []
        query = (
            select(BookTable)
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower),
            )
            .where(
                BookTable.id.in_(book_ids),
                BookTable.is_deleted.is_(False),
            )
        )
        result    = await self.session.execute(query)
        books_map = {b.id: b for b in result.scalars().all()}
        return [books_map[bid] for bid in book_ids if bid in books_map]

    async def update_book(self, book_id: int, update_data: BookUpdate):
        """
        Защита от пустого словаря значений.
        is_deleted в WHERE — предотвращает изменение книги,
        удалённой в параллельном запросе (race condition).
        """
        values = update_data.model_dump(exclude_unset=True)
        if not values:
            return

        stmt = (
            update(BookTable)
            .where(
                BookTable.id == book_id,
                BookTable.is_deleted.is_(False),
            )
            .values(**values)
        )
        await self.session.execute(stmt)

        if "genre" in values:
            invalidate_genres_cache()

    async def soft_delete_book(self, book_id: int):
        """
        Устанавливает одновременно is_deleted=True И status=DELETED.
        Статус и is_deleted всегда консистентны.

        Инвалидирует кэш жанров.
        """
        stmt = (
            update(BookTable)
            .where(BookTable.id == book_id)
            .values(is_deleted=True, status=BookStatus.DELETED)
        )
        await self.session.execute(stmt)
        invalidate_genres_cache()

    async def update_status(
        self,
        book_id: int,
        status: BookStatus,
        borrower_id: int = None,
        due_date: datetime = None,
        expected_status: BookStatus = None,
        expected_statuses: List[BookStatus] = None,
    ) -> bool:
        """
        Обновляет статус книги. Возвращает True если обновление применено.

        Параметры expected_status / expected_statuses реализуют оптимистичную
        блокировку: UPDATE применяется только если текущий статус совпадает.
        """
        values: dict = {"status": status}

        if status == BookStatus.AVAILABLE:
            values["borrower_id"]     = None
            values["return_due_date"] = None
        elif borrower_id is not None:
            values["borrower_id"] = borrower_id

        if due_date is not None:
            values["return_due_date"] = due_date

        stmt = update(BookTable).where(BookTable.id == book_id)

        if expected_status is not None:
            stmt = stmt.where(BookTable.status == expected_status)
        elif expected_statuses is not None:
            stmt = stmt.where(BookTable.status.in_(expected_statuses))

        stmt = stmt.values(**values).returning(BookTable.id)
        result = await self.session.execute(stmt)
        return result.scalar() is not None

    async def try_reserve(
        self,
        book_id: int,
        user_id: int,
        max_borrows: int,
    ) -> bool:
        """
        Атомарное бронирование + проверка лимита в одном UPDATE.

        ИЗВЕСТНОЕ ОГРАНИЧЕНИЕ (race condition):
        При одновременных запросах от ОДНОГО пользователя на РАЗНЫЕ книги
        оба UPDATE могут пройти проверку лимита одновременно.
        Митигировано пост-проверкой count_active_by_user() в LibraryService.
        """
        active_count_subq = (
            select(func.count())
            .where(
                BookTable.borrower_id == user_id,
                BookTable.status.in_([
                    BookStatus.RESERVED,
                    BookStatus.BORROWED,
                    BookStatus.OVERDUE,
                ]),
                BookTable.is_deleted.is_(False),
            )
            .correlate()
            .scalar_subquery()
        )

        stmt = (
            update(BookTable)
            .where(
                BookTable.id == book_id,
                BookTable.status == BookStatus.AVAILABLE,
                BookTable.is_deleted.is_(False),
                active_count_subq < max_borrows,
            )
            .values(status=BookStatus.RESERVED, borrower_id=user_id)
            .returning(BookTable.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar() is not None

    async def count_active_by_user(self, user_id: int) -> int:
        """
        Считает книги пользователя в статусах RESERVED/BORROWED/OVERDUE.
        Используется после try_reserve() для пост-проверки лимита.
        """
        result = await self.session.execute(
            select(func.count()).where(
                BookTable.borrower_id == user_id,
                BookTable.status.in_([
                    BookStatus.RESERVED,
                    BookStatus.BORROWED,
                    BookStatus.OVERDUE,
                ]),
                BookTable.is_deleted.is_(False),
            )
        )
        return result.scalar_one()

    # -----------------------------------------------------------------------
    # Чтение
    # -----------------------------------------------------------------------

    async def get_books(
        self,
        status: Optional[BookStatus] = None,
        genre:  Optional[str] = None,
        limit:  int = 50,
        offset: int = 0,
    ) -> List[BookTable]:
        query = (
            select(BookTable)
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower),
            )
            .where(BookTable.is_deleted.is_(False))
        )

        if status:
            query = query.where(BookTable.status == status)
        if genre:
            query = query.where(BookTable.genre == genre)

        query = query.order_by(desc(BookTable.created_at)).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def count_books(
        self,
        status: Optional[BookStatus] = None,
        genre:  Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """
        Подсчёт книг с теми же фильтрами что у get_books/get_my_books.
        """
        stmt = select(func.count()).where(BookTable.is_deleted.is_(False))

        if user_id is not None:
            stmt = stmt.where(
                or_(
                    BookTable.owner_id    == user_id,
                    BookTable.borrower_id == user_id,
                )
            )
        if status:
            stmt = stmt.where(BookTable.status == status)
        if genre:
            stmt = stmt.where(BookTable.genre == genre)

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_books_lightweight(
        self,
        limit:  int = 2000,
        status: Optional[BookStatus] = None,
        genre:  Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[dict]:
        """
        Облегчённый запрос — только id/title/author без JOIN-ов.
        Источник кандидатов для Python fuzzy-поиска.
        """
        stmt = (
            select(BookTable.id, BookTable.title, BookTable.author)
            .where(BookTable.is_deleted.is_(False))
        )
        if user_id is not None:
            stmt = stmt.where(
                or_(
                    BookTable.owner_id    == user_id,
                    BookTable.borrower_id == user_id,
                )
            )
        if status:
            stmt = stmt.where(BookTable.status == status)
        if genre:
            stmt = stmt.where(BookTable.genre == genre)

        stmt   = stmt.order_by(desc(BookTable.created_at)).limit(limit)
        result = await self.session.execute(stmt)
        return [
            {"id": r.id, "title": r.title, "author": r.author}
            for r in result
        ]

    async def get_my_books(
        self,
        user_id: int,
        status:  Optional[BookStatus] = None,
        genre:   Optional[str] = None,
        limit:   int = 100,
        offset:  int = 0,
    ) -> List[BookTable]:
        conditions = [
            BookTable.is_deleted.is_(False),
            or_(
                BookTable.owner_id    == user_id,
                BookTable.borrower_id == user_id,
            ),
        ]
        if status:
            conditions.append(BookTable.status == status)
        if genre:
            conditions.append(BookTable.genre == genre)

        query = (
            select(BookTable)
            .where(and_(*conditions))
            .options(
                selectinload(BookTable.owner),
                selectinload(BookTable.borrower),
            )
            .order_by(desc(BookTable.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_genres(self) -> List[str]:
        """
        ORDER BY genre для детерминированного порядка пользовательских жанров.

        Использует TTL-кэш с asyncio.Lock для предотвращения thundering herd.
        """
        global _genres_cache, _genres_cache_at

        now = time.monotonic()

        if _genres_cache is not None and (now - _genres_cache_at) < _GENRES_CACHE_TTL:
            return _genres_cache

        async with _get_genres_lock():
            now = time.monotonic()
            if _genres_cache is not None and (now - _genres_cache_at) < _GENRES_CACHE_TTL:
                return _genres_cache

            query  = (
                select(distinct(BookTable.genre))
                .where(BookTable.genre.isnot(None))
                .where(BookTable.is_deleted.is_(False))
                .order_by(BookTable.genre)
            )
            result = await self.session.execute(query)
            genres = [g for g in result.scalars().all() if g]

            defaults   = ["Семейная", "До Крещения", "После крещения", "Историческая"]
            all_genres = list(dict.fromkeys(defaults + genres))

            _genres_cache    = all_genres
            _genres_cache_at = now

        return _genres_cache

    # -----------------------------------------------------------------------
    # История
    # -----------------------------------------------------------------------

    async def log_history(
        self,
        book_id: int,
        user_id: int,
        status_to: BookStatus,
        comment: str,
        photo_path: str = None,
    ):
        """Добавляет запись в историю. Не коммитит — часть транзакции сервиса."""
        entry = BookHistoryTable(
            book_id=book_id,
            user_id=user_id,
            status_to=status_to,
            comment=comment,
            photo_proof_path=photo_path,
        )
        self.session.add(entry)

    async def get_history(
        self,
        book_id: int,
        limit:   int = 50,
        offset:  int = 0,
    ) -> List[BookHistoryTable]:
        query = (
            select(BookHistoryTable)
            .where(BookHistoryTable.book_id == book_id)
            .order_by(desc(BookHistoryTable.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def count_history(self, book_id: int) -> int:
        """Подсчёт записей истории для пагинации."""
        result = await self.session.execute(
            select(func.count()).where(BookHistoryTable.book_id == book_id)
        )
        return result.scalar_one()

    # -----------------------------------------------------------------------
    # Waitlist
    # Коммитят самостоятельно — вызываются напрямую из эндпоинтов.
    # -----------------------------------------------------------------------

    async def add_to_waitlist(self, book_id: int, user_id: int) -> bool:
        """
        ON CONFLICT DO NOTHING — дубли игнорируются на уровне БД.
        Возвращает True если запись добавлена, False если уже существовала.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = (
            pg_insert(WaitlistTable)
            .values(book_id=book_id, user_id=user_id)
            .on_conflict_do_nothing(constraint="uq_waitlist_book_user")
            .returning(WaitlistTable.id)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar() is not None

    async def get_waitlist_users(self, book_id: int) -> List[int]:
        """Возвращает пользователей в порядке добавления (FIFO)."""
        query = (
            select(WaitlistTable.user_id)
            .where(WaitlistTable.book_id == book_id)
            .order_by(WaitlistTable.created_at)
        )
        res = await self.session.execute(query)
        return res.scalars().all()

    async def pop_first_waiter(self, book_id: int) -> Optional[int]:
        """
        Атомарно извлекает первого по времени пользователя из очереди.
        Не коммитит — часть транзакции возврата/отклонения книги.
        """
        subquery = (
            select(WaitlistTable.id)
            .where(WaitlistTable.book_id == book_id)
            .order_by(WaitlistTable.created_at)
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            delete(WaitlistTable)
            .where(WaitlistTable.id == subquery)
            .returning(WaitlistTable.user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar()

    async def clear_waitlist(self, book_id: int):
        """Не коммитит — часть транзакции сервиса."""
        stmt = delete(WaitlistTable).where(WaitlistTable.book_id == book_id)
        await self.session.execute(stmt)

    async def remove_from_waitlist(self, book_id: int, user_id: int):
        """Коммитит самостоятельно — вызывается напрямую из эндпоинта."""
        stmt = delete(WaitlistTable).where(
            and_(WaitlistTable.book_id == book_id, WaitlistTable.user_id == user_id)
        )
        await self.session.execute(stmt)
        await self.session.commit()
