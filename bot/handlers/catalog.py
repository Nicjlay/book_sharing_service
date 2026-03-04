"""
Handler для просмотра каталога книг (ТЗ 3.2.1)
"""
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api.client import APIError, api
from config import settings
from keyboards.inline import (
    book_card_keyboard,
    catalog_filters_keyboard,
    genres_keyboard,
    main_menu_keyboard,
)
from utils.formatters import escape_html, format_book_card
from utils.telegram import get_photo_input, safe_delete_message, safe_edit_message

logger = logging.getLogger(__name__)
router = Router()

_STATUS_EMOJI = {
    "available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"
}
_PER_PAGE = 5
# 200 — разумный компромисс между полнотой каталога и размером данных в FSM state.
# При 50 книгах пользователь видел максимум 10 страниц — остальные недоступны.
_CATALOG_LIMIT = 200


@router.message(Command("catalog"))
@router.callback_query(F.data == "catalog")
async def show_catalog(event: Message | CallbackQuery, state: FSMContext):
    """Открыть каталог с фильтрами (ТЗ 3.2.1)"""
    await state.clear()
    text = "📚 <b>Каталог книг</b>\n\nВыберите фильтр для просмотра книг:"

    if isinstance(event, CallbackQuery):
        await safe_edit_message(event.message, text, reply_markup=catalog_filters_keyboard())
        await event.answer()
    else:
        await event.answer(text, reply_markup=catalog_filters_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("filter:"))
async def apply_filter(callback: CallbackQuery, state: FSMContext):
    """Применить фильтр к каталогу"""
    filter_type = callback.data.split(":", 1)[1]

    try:
        if filter_type == "all":
            books = await api.get_books(limit=_CATALOG_LIMIT)
            title = "📚 Все книги"

        elif filter_type == "available":
            books = await api.get_books(status="available", limit=_CATALOG_LIMIT)
            title = "🟢 Доступные книги"

        elif filter_type == "genres":
            genres = await api.get_genres()
            # Сохраняем список жанров в FSM state — нужен для индексного lookup
            # в filter_by_genre (callback_data использует genre_idx:N вместо genre:name,
            # чтобы не выходить за лимит 64 байта для кириллических жанров).
            await state.update_data(genres_list=genres)
            await safe_edit_message(
                callback.message,
                "📖 <b>Выберите жанр:</b>",
                reply_markup=genres_keyboard(genres),
            )
            await callback.answer()
            return

        else:
            await callback.answer("Неизвестный фильтр", show_alert=True)
            return

        if not books:
            await safe_edit_message(
                callback.message,
                f"{title}\n\n📭 Книги не найдены",
                reply_markup=catalog_filters_keyboard(),
            )
            await callback.answer()
            return

        await state.update_data(books=books, current_page=0, filter_title=title)
        await show_books_page(callback.message, books, 0, title)
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки каталога", show_alert=True)
        logger.error("Error in apply_filter (%s): %s", filter_type, e)


@router.callback_query(F.data.startswith("genre_idx:"))
async def filter_by_genre(callback: CallbackQuery, state: FSMContext):
    """
    Фильтр по жанру через индекс.

    Используем индекс вместо имени жанра в callback_data:
    кириллические жанры в UTF-8 занимают 2 байта/символ, что при длинных
    названиях превышает лимит Telegram 64 байта → BadRequest.
    """
    try:
        idx = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный индекс жанра", show_alert=True)
        return

    data = await state.get_data()
    genres_list = data.get("genres_list", [])

    # Если FSM state сбросился (перезапуск бота / длительная сессия) —
    # перезагружаем список жанров из API
    if not genres_list:
        try:
            genres_list = await api.get_genres()
            await state.update_data(genres_list=genres_list)
        except Exception as e:
            await callback.answer("Список жанров устарел, попробуйте снова", show_alert=True)
            logger.error("Error reloading genres: %s", e)
            return

    if idx < 0 or idx >= len(genres_list):
        await callback.answer("Жанр не найден", show_alert=True)
        return

    genre = genres_list[idx]

    try:
        books = await api.get_books(genre=genre, limit=_CATALOG_LIMIT)
        title = f"📚 Жанр: {escape_html(genre)}"

        if not books:
            await safe_edit_message(
                callback.message,
                f"{title}\n\n📭 Книги не найдены",
                reply_markup=catalog_filters_keyboard(),
            )
            await callback.answer()
            return

        await state.update_data(books=books, current_page=0, filter_title=title)
        await show_books_page(callback.message, books, 0, title)
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки", show_alert=True)
        logger.error("Error in filter_by_genre (%s): %s", genre, e)


async def show_books_page(message: Message, books: list, page: int, title: str):
    """Показать страницу с книгами"""
    total_pages = max(1, (len(books) - 1) // _PER_PAGE + 1)
    page = max(0, min(page, total_pages - 1))

    start = page * _PER_PAGE
    end = start + _PER_PAGE
    page_books = books[start:end]

    builder = InlineKeyboardBuilder()

    text = f"{title}\n\n"
    text += f"📄 Найдено: {len(books)} книг · Страница {page + 1}/{total_pages}\n\n"

    for book in page_books:
        emoji = _STATUS_EMOJI.get(book.get("status", "available"), "⚪️")
        safe_title = escape_html(book.get("title", "—"))
        safe_author = escape_html(book.get("author", "—"))
        text += f"{emoji} <b>{safe_title}</b>\n"
        text += f"   ✍️ {safe_author}\n"
        # FIX: book.get("id", 0) вместо book["id"] — KeyError если id отсутствует
        book_id = book.get("id", 0)
        text += f"   🔖 ID: #{book_id:05d}\n\n"

        # FIX: book.get("title", "—") вместо book["title"] — KeyError если title отсутствует
        raw_title = book.get("title", "—")
        btn_label = raw_title[:30] + ("…" if len(raw_title) > 30 else "")
        builder.row(InlineKeyboardButton(
            text=f"📖 {btn_label}",
            callback_data=f"book:{book_id}",
        ))

    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"page:{page + 1}"))
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="🔙 К фильтрам", callback_data="catalog"))

    await safe_edit_message(message, text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("page:"))
async def change_page(callback: CallbackQuery, state: FSMContext):
    """Пагинация"""
    try:
        page = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректная страница", show_alert=True)
        return

    if page < 0:
        await callback.answer("Уже первая страница", show_alert=True)
        return

    data = await state.get_data()
    books = data.get("books", [])
    title = data.get("filter_title", "📚 Книги")

    if not books:
        await callback.answer("Список книг устарел, вернитесь к каталогу", show_alert=True)
        return

    total_pages = max(1, (len(books) - 1) // _PER_PAGE + 1)
    if page >= total_pages:
        await callback.answer("Уже последняя страница", show_alert=True)
        return

    await state.update_data(current_page=page)
    await show_books_page(callback.message, books, page, title)
    await callback.answer()


@router.callback_query(F.data.startswith("book:"))
async def show_book_detail(callback: CallbackQuery):
    """Показать детальную карточку книги"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID книги", show_alert=True)
        return

    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_set

    try:
        book = await api.get_book(book_id)
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        text = format_book_card(book)

        # FIX: определяем in_waitlist из ответа API, чтобы показать правильную кнопку.
        # API может вернуть поле "in_waitlist" (bool) или "waitlist_user_ids" (list[int]).
        # Проверяем оба варианта для совместимости с разными версиями API.
        in_waitlist: bool = bool(book.get("in_waitlist", False))
        if not in_waitlist:
            waitlist_ids = book.get("waitlist_user_ids") or book.get("waitlist_ids") or []
            if isinstance(waitlist_ids, list):
                in_waitlist = user_id in waitlist_ids

        if book.get("image_path"):
            try:
                photo = await get_photo_input(book["image_path"])
                if photo:
                    await safe_delete_message(callback.message)
                    await callback.message.answer_photo(
                        photo=photo,
                        caption=text,
                        reply_markup=book_card_keyboard(book, user_id, is_admin, in_waitlist),
                        parse_mode="HTML",
                    )
                    await callback.answer()
                    return
            except Exception as e:
                logger.warning("Photo load error for book %d: %s", book_id, e)

        await safe_edit_message(
            callback.message,
            text,
            reply_markup=book_card_keyboard(book, user_id, is_admin, in_waitlist),
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки книги", show_alert=True)
        logger.error("Error in show_book_detail (book %d): %s", book_id, e)