"""
Handler поиска книг с нечётким триграммным поиском (UX-friendly)
"""
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api.client import api
from config import settings
from keyboards.inline import main_menu_keyboard
from states.wizard import SearchStates
from utils.formatters import escape_html
from utils.telegram import safe_delete_message, safe_edit_message

logger = logging.getLogger(__name__)
router = Router()

MIN_QUERY_LEN = 2


def _pluralize_books(count: int) -> str:
    """Правильное склонение слова 'книга' для русского языка."""
    if 11 <= count % 100 <= 19:
        return "книг"
    rem = count % 10
    if rem == 1:
        return "книга"
    if 2 <= rem <= 4:
        return "книги"
    return "книг"


# ---------------------------------------------------------------------------
# Вход в поиск
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "search")
async def start_search(callback: CallbackQuery, state: FSMContext):
    """Запрашиваем текст поиска"""
    await state.set_state(SearchStates.query)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu"))

    await safe_edit_message(
        callback.message,
        "🔍 <b>Поиск книги</b>\n\n"
        "Введите название или автора.\n"
        "Поиск устойчив к опечаткам — не бойтесь ошибаться 😊",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.message(Command("search"))
async def search_command(message: Message, state: FSMContext):
    """Поиск через команду /search"""
    await state.set_state(SearchStates.query)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu"))

    await message.answer(
        "🔍 <b>Поиск книги</b>\n\n"
        "Введите название или автора.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Обработка запроса
# ---------------------------------------------------------------------------

@router.message(SearchStates.query)
async def process_search_query(message: Message, state: FSMContext):
    """Выполняем поиск и показываем результаты"""
    query = message.text.strip() if message.text else ""
    user_id = message.from_user.id
    is_admin = user_id in settings.admin_ids_set

    if len(query) < MIN_QUERY_LEN:
        await message.answer(
            f"❌ Запрос слишком короткий. Введите минимум {MIN_QUERY_LEN} символа.",
            parse_mode="HTML",
        )
        return

    loading = await message.answer("🔍 Ищем...")

    try:
        books = await api.get_books(query=query)
    except Exception as e:
        await safe_delete_message(loading)
        await message.answer(
            "❌ Ошибка поиска. Попробуйте позже.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )
        logger.error("Search error for query %r: %s", query, e)
        return

    await safe_delete_message(loading)
    await state.clear()

    safe_query = escape_html(query)

    if not books:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔍 Попробовать снова", callback_data="search"))
        builder.row(InlineKeyboardButton(text="📖 Каталог", callback_data="catalog"))
        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))

        await message.answer(
            f"😔 <b>По запросу «{safe_query}» ничего не найдено</b>\n\n"
            f"Попробуйте:\n"
            f"• Проверить написание\n"
            f"• Ввести только часть слова\n"
            f"• Поискать по автору вместо названия",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    count = len(books)
    word = _pluralize_books(count)
    header = f"📚 <b>Найдено {count} {word}</b> по запросу «{safe_query}»:\n\n"

    status_emoji = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}
    builder = InlineKeyboardBuilder()

    text = header
    for i, book in enumerate(books[:10], 1):
        emoji = status_emoji.get(book.get("status", "available"), "⚪️")
        title = book.get("title", "—")
        author = book.get("author", "—")
        # FIX: book.get("id", 0) вместо book["id"] — KeyError если id отсутствует
        book_id = book.get("id", 0)

        btn_label = title if len(title) <= 32 else title[:30] + ".."
        builder.row(InlineKeyboardButton(
            text=f"{emoji} {btn_label}",
            callback_data=f"book:{book_id}",
        ))

        text += f"{i}. {emoji} <b>{escape_html(title)}</b>\n"
        text += f"   ✍️ {escape_html(author)}\n"
        status = book.get("status")
        if status == "available":
            text += "   <i>Доступна</i>\n"
        elif status in ("borrowed", "overdue"):
            text += "   <i>Выдана</i>\n"
        elif status == "reserved":
            text += "   <i>Забронирована</i>\n"
        text += "\n"

    if count > 10:
        text += f"<i>...и ещё {count - 10}. Уточните запрос для точного поиска.</i>\n"

    builder.row(InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))

    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
