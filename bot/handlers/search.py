"""
Handler поиска книг с нечётким триграммным поиском (UX-friendly)
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from api.client import api
from states.wizard import SearchStates
from keyboards.inline import main_menu_keyboard, cancel_keyboard
from config import settings

router = Router()

# Минимальная длина запроса
MIN_QUERY_LEN = 2
# Порог "хорошего" совпадения для вывода подсказки
HINT_THRESHOLD = 3   # если найдено меньше N — покажем подсказку


# ---------------------------------------------------------------------------
# Вход в поиск
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "search")
async def start_search(callback: CallbackQuery, state: FSMContext):
    """Запрашиваем текст поиска"""
    await state.set_state(SearchStates.query)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu"))

    await callback.message.edit_text(
        "🔍 <b>Поиск книги</b>\n\n"
        "Введите название или автора.\n"
        "Поиск устойчив к опечаткам — не бойтесь ошибаться 😊",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(Command("search"))
async def search_command(message: Message, state: FSMContext):
    """Поиск через команду /search"""
    await state.set_state(SearchStates.query)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu"))

    await message.answer(
        "🔍 <b>Поиск книги</b>\n\n"
        "Введите название или автора.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# ---------------------------------------------------------------------------
# Обработка запроса
# ---------------------------------------------------------------------------

@router.message(SearchStates.query)
async def process_search_query(message: Message, state: FSMContext):
    """Выполняем поиск и показываем результаты"""
    query = message.text.strip() if message.text else ""
    user_id = message.from_user.id
    is_admin = user_id in settings.admin_ids_list

    if len(query) < MIN_QUERY_LEN:
        await message.answer(
            f"❌ Запрос слишком короткий. Введите минимум {MIN_QUERY_LEN} символа.",
            parse_mode="HTML"
        )
        return

    # Индикатор загрузки
    loading = await message.answer("🔍 Ищем...")

    try:
        books = await api.get_books(query=query)
    except Exception as e:
        await loading.delete()
        await message.answer(
            "❌ Ошибка поиска. Попробуйте позже.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML"
        )
        print(f"Search error: {e}")
        return

    await loading.delete()
    await state.clear()

    # ── Ничего не найдено ──────────────────────────────────────────────────
    if not books:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔍 Попробовать снова", callback_data="search"))
        builder.row(InlineKeyboardButton(text="📖 Каталог", callback_data="catalog"))
        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))

        await message.answer(
            f"😔 <b>По запросу «{query}» ничего не найдено</b>\n\n"
            f"Попробуйте:\n"
            f"• Проверить написание\n"
            f"• Ввести только часть слова\n"
            f"• Поискать по автору вместо названия",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return

    # ── Результаты ────────────────────────────────────────────────────────
    count = len(books)

    # Заголовок с подсказкой качества
    if count == 1:
        header = f"✅ <b>Найдена 1 книга</b> по запросу «{query}»:\n\n"
    elif count < HINT_THRESHOLD:
        header = f"🔎 <b>Найдено {count} книги</b> по запросу «{query}»:\n\n"
    else:
        header = f"📚 <b>Найдено {count} книг</b> по запросу «{query}»:\n\n"

    status_emoji = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}

    # Строим клавиатуру и текст-список
    builder = InlineKeyboardBuilder()

    text = header
    for i, book in enumerate(books[:10], 1):
        emoji = status_emoji.get(book.get("status", "available"), "⚪️")
        title = book.get("title", "—")
        author = book.get("author", "—")

        # Обрезаем длинные названия для кнопки
        btn_label = title if len(title) <= 32 else title[:30] + ".."
        builder.row(InlineKeyboardButton(
            text=f"{emoji} {btn_label}",
            callback_data=f"book:{book['id']}"
        ))

        # Текст-список (краткий)
        text += f"{i}. {emoji} <b>{title}</b>\n"
        text += f"   ✍️ {author}\n"
        if book.get("status") == "available":
            text += f"   <i>Доступна</i>\n"
        elif book.get("status") in ("borrowed", "overdue"):
            text += f"   <i>Выдана</i>\n"
        elif book.get("status") == "reserved":
            text += f"   <i>Забронирована</i>\n"
        text += "\n"

    if count > 10:
        text += f"<i>...и ещё {count - 10}. Уточните запрос для точного поиска.</i>\n"

    builder.row(InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))

    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
