"""
Handler для визарда добавления книги (ТЗ 3.1)
Пошаговый диалог из 6 шагов
"""
import io
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api.client import APIError, api
from config import settings
from keyboards.inline import (
    cancel_keyboard,
    main_menu_keyboard,
    users_selection_keyboard,
    wizard_confirm_keyboard,
    wizard_skip_photo_keyboard,
)
from states.wizard import AddBookWizard
from utils.formatters import escape_html, format_wizard_preview
from utils.telegram import safe_delete_message, safe_edit_message
from utils.validators import validate_author, validate_description, validate_title

from .admin import admin_only

logger = logging.getLogger(__name__)
router = Router()

# Жанры по умолчанию — используются если API-жанры не содержат ни одного
# из common_genres или если запрос жанров упал.
_FALLBACK_GENRES = ["Семейная", "До крещения", "После крещения", "Историческая"]

# Максимальное число байт для callback_data жанра с учётом префикса.
# Telegram ограничивает callback_data 64 байтами.
# Используем индексный подход: "wizard_genre_idx:N" — максимум 22 байта.
_WIZARD_GENRE_PREFIX = "wizard_genre_idx:"


# ==========================================
# 1. ЗАПУСК ВИЗАРДА
# ==========================================

@router.message(Command("add"))
@router.callback_query(F.data == "add_book")
@admin_only
async def start_add_book_wizard(event: Message | CallbackQuery, state: FSMContext, **kwargs):
    """Начало визарда добавления книги — Шаг 1: Автор"""
    await state.set_state(AddBookWizard.author)
    await state.update_data(wizard_data={})

    text = (
        "📖 <b>Добавление новой книги</b>\n\n"
        "📝 <b>Шаг 1/6:</b> Автор книги\n\n"
        "Введите <b>фамилию и имя автора</b>\n"
        "(например: <i>Достоевский Федор</i>)\n\n"
        "⚠️ Необходимо указать минимум два слова"
    )

    if isinstance(event, CallbackQuery):
        await safe_edit_message(event.message, text, reply_markup=cancel_keyboard())
        await event.answer()
    else:
        await event.answer(text, reply_markup=cancel_keyboard(), parse_mode="HTML")


# ==========================================
# 2. АВТОР
# ==========================================

@router.message(AddBookWizard.author)
async def process_author(message: Message, state: FSMContext):
    author = message.text.strip() if message.text else ""
    is_valid, error_msg = validate_author(author)
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return

    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["author"] = author
    await state.update_data(wizard_data=wizard_data)
    await state.set_state(AddBookWizard.title)

    await message.answer(
        f"✅ Автор: <b>{escape_html(author)}</b>\n\n"
        f"📝 <b>Шаг 2/6:</b> Название книги\n\n"
        f"Введите полное название книги:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


# ==========================================
# 3. НАЗВАНИЕ
# ==========================================

@router.message(AddBookWizard.title)
async def process_title(message: Message, state: FSMContext):
    title = message.text.strip() if message.text else ""
    is_valid, error_msg = validate_title(title)
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return

    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["title"] = title
    await state.update_data(wizard_data=wizard_data)
    await state.set_state(AddBookWizard.photo)

    await message.answer(
        f"✅ Название: <b>{escape_html(title)}</b>\n\n"
        f"📝 <b>Шаг 3/6:</b> Фото обложки\n\n"
        f"Загрузите фотографию обложки книги (до 5 МБ)\n\n"
        f"⚠️ Принимаются только изображения (JPG, PNG, WEBP)\n\n"
        f"Можно пропустить — будет использована заглушка",
        reply_markup=wizard_skip_photo_keyboard(),
        parse_mode="HTML",
    )


# ==========================================
# 4. ФОТО
# ==========================================

@router.callback_query(F.data == "skip_photo", AddBookWizard.photo)
async def skip_photo(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Фото пропущено")
    await _go_to_description(callback.message, state)


@router.message(AddBookWizard.photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    """
    Обработка загруженного фото с немедленной загрузкой в API.

    Фото загружается в API сразу и в state сохраняется только image_path
    (не байты), что экономит память MemoryStorage / Redis.

    Если загрузка не удалась — пользователь видит ошибку и может
    повторить попытку или пропустить шаг, не прерывая визард.
    """
    photo = message.photo[-1]

    if photo.file_size and photo.file_size > 5 * 1024 * 1024:
        await message.answer(
            "❌ Файл слишком большой (максимум 5 МБ)\n"
            "Попробуйте загрузить другое фото или пропустите этот шаг.",
        )
        return

    loading = await message.answer("⏳ Загружаем фото обложки...")

    try:
        file_io = await message.bot.download(photo.file_id)

        if isinstance(file_io, io.BytesIO):
            photo_bytes = file_io.getvalue()
        else:
            file_io.seek(0)
            photo_bytes = file_io.read()

        if not photo_bytes:
            raise ValueError("Получен пустой файл")

        media_result = await api.upload_media(photo_bytes, filename="cover.jpg")
        image_path = (
            media_result.get("path")
            or media_result.get("image_path")
            or media_result.get("url")
        )

        if not image_path:
            raise ValueError("API не вернуло путь к изображению")

        data = await state.get_data()
        wizard_data = data.get("wizard_data", {})
        wizard_data["image_path"] = image_path
        wizard_data["has_photo"] = True
        await state.update_data(wizard_data=wizard_data)

        await safe_delete_message(loading)
        await message.answer("✅ Фото обложки загружено!")
        await _go_to_description(message, state)

    except APIError as e:
        await safe_delete_message(loading)
        logger.error("Error uploading photo in wizard: HTTP %d %s", e.status, e.message)
        if e.status == 413:
            user_msg = "❌ Фото слишком большое. Уменьшите размер или пропустите этот шаг."
        elif e.status == 415:
            user_msg = "❌ Неподдерживаемый формат. Используйте JPG, PNG или WebP."
        else:
            user_msg = "❌ Не удалось загрузить фото. Попробуйте ещё раз или пропустите."
        await message.answer(user_msg, reply_markup=wizard_skip_photo_keyboard())
    except Exception as e:
        await safe_delete_message(loading)
        logger.error("Error processing photo in wizard: %s", e)
        await message.answer(
            "❌ Не удалось обработать фото. Попробуйте ещё раз или пропустите этот шаг.",
            reply_markup=wizard_skip_photo_keyboard(),
        )


@router.message(AddBookWizard.photo)
async def process_document_as_photo(message: Message):
    await message.answer(
        "⚠️ Пожалуйста, отправьте изображение как <b>Фото</b> (со сжатием), а не как Файл.",
        parse_mode="HTML",
    )


async def _go_to_description(message: Message, state: FSMContext):
    """
    Переход к шагу выбора жанра/описания.

    FIX: Ранее жанры передавались напрямую в callback_data:
    f"select_genre:{genre}". Длинные кириллические названия жанров
    (2 байта/символ в UTF-8) превышали лимит Telegram 64 байта → BadRequest.

    Решение: используем индексный подход — f"{_WIZARD_GENRE_PREFIX}{i}",
    что гарантированно помещается в 64 байта (максимум ~22 байта).
    Список жанров сохраняется в FSM state под ключом "wizard_genres_list".
    """
    await state.set_state(AddBookWizard.description)

    try:
        genres = await api.get_genres()
        if isinstance(genres, dict):
            genres = genres.get("genres", [])
    except Exception as e:
        logger.warning("Error getting genres in wizard: %s", e)
        genres = []

    builder = InlineKeyboardBuilder()

    if genres:
        filtered = [g for g in _FALLBACK_GENRES if g in genres]
        display_genres = filtered if filtered else _FALLBACK_GENRES
    else:
        display_genres = _FALLBACK_GENRES

    # FIX: сохраняем display_genres в FSM state для индексного lookup
    await state.update_data(wizard_genres_list=display_genres)

    for i, genre in enumerate(display_genres):
        # FIX: индекс вместо имени жанра — защита от превышения 64-байтного лимита
        builder.row(InlineKeyboardButton(
            text=f"📚 {genre}",
            callback_data=f"{_WIZARD_GENRE_PREFIX}{i}",
        ))

    builder.row(InlineKeyboardButton(text="✏️ Ввести свой жанр", callback_data="select_genre:custom"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="wizard_cancel"))

    await message.answer(
        "📝 <b>Шаг 4/6:</b> Жанр книги\n\n"
        "Выберите жанр из списка или введите свой.\n\n"
        "✏️ При вводе текстом — <b>максимум 10 слов</b>.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ==========================================
# 5. ЖАНР
# ==========================================
@router.callback_query(F.data.startswith(_WIZARD_GENRE_PREFIX), AddBookWizard.description)
async def select_genre_by_index(callback: CallbackQuery, state: FSMContext):
    """
    Выбор жанра по индексу из FSM state.

    FIX: Используем индексный подход вместо имени в callback_data.
    Если FSM state сбросился — перезагружаем жанры из API.
    """
    try:
        idx = int(callback.data[len(_WIZARD_GENRE_PREFIX):])
    except (ValueError, IndexError):
        await callback.answer("Некорректный индекс жанра", show_alert=True)
        return

    data = await state.get_data()
    display_genres = data.get("wizard_genres_list", [])

    # Если список жанров устарел (сброс FSM) — перезагружаем
    if not display_genres:
        try:
            api_genres = await api.get_genres()
            if isinstance(api_genres, dict):
                api_genres = api_genres.get("genres", [])
            filtered = [g for g in _FALLBACK_GENRES if g in api_genres]
            display_genres = filtered if filtered else _FALLBACK_GENRES
            await state.update_data(wizard_genres_list=display_genres)
        except Exception as e:
            logger.warning("Error reloading genres in wizard: %s", e)
            display_genres = _FALLBACK_GENRES

    if idx < 0 or idx >= len(display_genres):
        await callback.answer("Жанр не найден, попробуйте снова", show_alert=True)
        return

    genre = display_genres[idx]

    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["genre"] = genre
    await state.update_data(wizard_data=wizard_data)

    await callback.answer(f"Выбран жанр: {genre}")
    await _go_to_owner(callback.message, state)


@router.callback_query(F.data == "select_genre:custom", AddBookWizard.description)
async def select_genre_custom(callback: CallbackQuery, state: FSMContext):
    """Ввод произвольного жанра текстом."""
    await safe_edit_message(
        callback.message,
        "✍️ <b>Введите жанр текстом</b>\n\n"
        "Например: <i>Современная проза</i>, <i>Исторический детектив</i>\n\n"
        "⚠️ Максимум <b>10 слов</b>.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AddBookWizard.description)
async def process_description(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""

    if not text:
        await message.answer(
            "❌ Жанр не может быть пустым. Введите текст или выберите из списка.",
            parse_mode="HTML",
        )
        return

    words = text.split()
    if len(words) > 10:
        await message.answer(
            f"❌ Слишком длинно — <b>{len(words)} слов</b>.\n\n"
            f"Максимум <b>10 слов</b>. Попробуйте короче:\n"
            f"<i>{escape_html(text)}</i>",
            parse_mode="HTML",
        )
        return

    if len(text) > 100:
        await message.answer("❌ Жанр слишком длинный (максимум 100 символов).")
        return

    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    # Сохраняем как genre (обязательное поле API), не как description
    wizard_data["genre"] = text
    await state.update_data(wizard_data=wizard_data)
    await message.answer(f"✅ Жанр: <b>{escape_html(text)}</b>", parse_mode="HTML")
    await _go_to_owner(message, state)


async def _go_to_owner(message: Message, state: FSMContext):
    """Переход к выбору владельца"""
    await state.set_state(AddBookWizard.owner)

    try:
        users = await api.search_users()
    except Exception as e:
        logger.warning("Error loading users for wizard: %s", e)
        users = []

    if not users:
        await message.answer(
            "📝 <b>Шаг 5/6:</b> Владелец книги\n\n"
            "Введите username или имя владельца для поиска:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.update_data(all_users=users)
    await message.answer(
        "📝 <b>Шаг 5/6:</b> Владелец книги\n\n"
        "Выберите владельца из списка или введите имя для поиска:",
        reply_markup=users_selection_keyboard(users),
        parse_mode="HTML",
    )


# ==========================================
# 6. ВЛАДЕЛЕЦ
# ==========================================

@router.message(AddBookWizard.owner)
async def search_owner(message: Message, state: FSMContext):
    query = message.text.strip() if message.text else ""
    if len(query) < 2:
        await message.answer("❌ Введите минимум 2 символа для поиска.")
        return
    try:
        users = await api.search_users(query)
        if not users:
            safe_q = escape_html(query)
            await message.answer(f"🔍 Никого не нашли по запросу «{safe_q}».", parse_mode="HTML")
            return

        await state.update_data(all_users=users)
        await message.answer("🔍 Результаты поиска:", reply_markup=users_selection_keyboard(users))
    except Exception as e:
        logger.error("Error searching users in wizard: %s", e)
        await message.answer("❌ Ошибка поиска. Попробуйте ещё раз.")


@router.callback_query(F.data.startswith("select_owner:"), AddBookWizard.owner)
async def select_owner(callback: CallbackQuery, state: FSMContext):
    try:
        owner_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID пользователя", show_alert=True)
        return

    data = await state.get_data()
    all_users = data.get("all_users", [])

    owner = next((u for u in all_users if u.get("id") == owner_id), None)

    if not owner:
        # Кеш не содержит пользователя — перезагружаем из API
        try:
            fresh_users = await api.search_users(limit=100)
            owner = next((u for u in fresh_users if u.get("id") == owner_id), None)
            if fresh_users:
                await state.update_data(all_users=fresh_users)
        except Exception as e:
            logger.error("Error fetching users for owner lookup: %s", e)

    if not owner:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    owner_name = owner.get("username") or owner.get("full_name", "Неизвестный")

    wizard_data = data.get("wizard_data", {})
    wizard_data["owner_id"] = owner_id
    wizard_data["owner_name"] = owner_name
    await state.update_data(wizard_data=wizard_data)

    await callback.answer(f"Выбран: {owner_name}")
    await _show_confirmation(callback.message, state)


# ==========================================
# 7. ПОДТВЕРЖДЕНИЕ И СОЗДАНИЕ
# ==========================================

async def _show_confirmation(message: Message, state: FSMContext):
    """Шаг 6: Предпросмотр и подтверждение"""
    await state.set_state(AddBookWizard.confirm)
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    preview_text = format_wizard_preview(wizard_data)
    await message.answer(preview_text, reply_markup=wizard_confirm_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "wizard_confirm", AddBookWizard.confirm)
async def confirm_and_create_book(callback: CallbackQuery, state: FSMContext):
    """Финальное создание книги"""
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})

    if not wizard_data.get("title") or not wizard_data.get("author") or not wizard_data.get("owner_id"):
        await callback.answer("Данные визарда устарели. Начните заново.", show_alert=True)
        await state.clear()
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("⏳ Создаю книгу, подождите...")

    try:
        book_fields = {
            "title": wizard_data["title"],
            "author": wizard_data["author"],
            "owner_id": wizard_data["owner_id"],
            "description": wizard_data.get("description"),
            # genre обязателен для API; на шаге 4 всегда сохраняется,
            # fallback "Другое" защищает от edge-case сброса FSM state.
            "genre": wizard_data.get("genre") or "Другое",
            "image_path": wizard_data.get("image_path"),
        }
        book_fields = {k: v for k, v in book_fields.items() if v is not None}

        book = await api.create_book(book_fields)

        if not book:
            raise ValueError("API вернуло пустой ответ")

        await state.clear()

        is_admin = callback.from_user.id in settings.admin_ids_set
        safe_title = escape_html(book.get("title", "Без названия"))
        await callback.message.answer(
            f"✅ <b>Книга успешно добавлена!</b>\n\n"
            f"📖 {safe_title}\n"
            f"🔖 ID: #{book.get('id', '???')}",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(is_admin),
        )

    except APIError as e:
        logger.error("Error creating book: HTTP %d %s", e.status, e.message)
        if e.status == 413:
            user_msg = "❌ Фото слишком большое. Попробуйте с меньшим изображением."
        elif e.status == 415:
            user_msg = "❌ Неподдерживаемый формат фото. Используйте JPEG, PNG или WebP."
        elif e.status == 422:
            user_msg = "❌ Некорректные данные книги. Проверьте введённые поля."
        elif e.status == 403:
            user_msg = "❌ Нет прав на добавление книги."
        else:
            user_msg = "❌ Ошибка создания книги. Попробуйте ещё раз."
        await callback.answer("Ошибка создания книги", show_alert=True)
        await callback.message.answer(user_msg)
    except Exception as e:
        logger.error("Error creating book: %s", e)
        await callback.answer("Ошибка создания книги", show_alert=True)
        await callback.message.answer("❌ Ошибка создания книги. Попробуйте ещё раз.")


@router.callback_query(F.data == "wizard_edit", AddBookWizard.confirm)
async def edit_wizard(callback: CallbackQuery, state: FSMContext):
    """
    Вернуться к редактированию — начать визард заново.

    FIX: сохраняем image_path из предыдущего прохода визарда.
    Исходная версия обнуляла весь wizard_data включая image_path, что:
    1. Оставляло уже загруженный файл в хранилище без ссылки (orphan).
    2. Вынуждало пользователя повторно загружать фото на шаге 3.

    Теперь image_path сохраняется. Если пользователь дойдёт до шага фото
    и загрузит новое — image_path перезапишется. Если пропустит — старое
    фото будет использовано при создании книги.
    """
    data = await state.get_data()
    old_wizard_data = data.get("wizard_data", {})

    # Сохраняем только фото — оно уже загружено в хранилище.
    # Остальные поля (author, title, genre, description) вводятся заново.
    preserved: dict = {}
    if old_wizard_data.get("image_path"):
        preserved["image_path"] = old_wizard_data["image_path"]
        preserved["has_photo"] = old_wizard_data.get("has_photo", True)

    await state.update_data(wizard_data=preserved)
    await state.set_state(AddBookWizard.author)

    photo_hint = " (фото обложки сохранено)" if preserved.get("image_path") else ""
    await safe_edit_message(
        callback.message,
        f"✏️ <b>Редактирование</b>{escape_html(photo_hint)}\n\n"
        f"Начнём сначала. Введите автора книги:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "wizard_cancel")
async def cancel_wizard(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_set

    await safe_delete_message(callback.message)
    await callback.message.answer(
        "❌ Добавление отменено",
        reply_markup=main_menu_keyboard(is_admin),
    )
    await callback.answer()