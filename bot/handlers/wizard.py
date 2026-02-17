"""
Handler для визарда добавления книги (ТЗ 3.1)
Пошаговый диалог из 6 шагов
"""
import io
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from api.client import api
from states.wizard import AddBookWizard
from keyboards.inline import (
    wizard_skip_photo_keyboard,
    genres_keyboard,
    users_selection_keyboard,
    wizard_confirm_keyboard,
    cancel_keyboard,
    main_menu_keyboard
)
from utils.validators import validate_author, validate_title, validate_description
from utils.formatters import format_wizard_preview
from config import settings

from .admin import admin_only

router = Router()

# ==========================================
# 1. ЗАПУСК ВИЗАРДА
# ==========================================

@router.message(Command("add"))
@router.callback_query(F.data == "add_book")
@admin_only
async def start_add_book_wizard(event: Message | CallbackQuery, state: FSMContext, **kwargs):
    """
    Начало визарда добавления книги
    Шаг 1: Запрос автора
    """
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
        await event.message.edit_text(text, reply_markup=cancel_keyboard(), parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=cancel_keyboard(), parse_mode="HTML")


# ==========================================
# 2. АВТОР
# ==========================================

@router.message(AddBookWizard.author)
async def process_author(message: Message, state: FSMContext):
    author = message.text.strip()
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
        f"✅ Автор: <b>{author}</b>\n\n"
        f"📝 <b>Шаг 2/6:</b> Название книги\n\n"
        f"Введите полное название книги:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )


# ==========================================
# 3. НАЗВАНИЕ
# ==========================================

@router.message(AddBookWizard.title)
async def process_title(message: Message, state: FSMContext):
    title = message.text.strip()
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
        f"✅ Название: <b>{title}</b>\n\n"
        f"📝 <b>Шаг 3/6:</b> Фото обложки\n\n"
        f"Загрузите фотографию обложки книги (до 5 МБ)\n\n"
        f"⚠️ Принимаются только изображения (JPG, PNG, WEBP)\n\n"
        f"Можно пропустить этот шаг - будет использована заглушка",
        reply_markup=wizard_skip_photo_keyboard(),
        parse_mode="HTML"
    )


# ==========================================
# 4. ФОТО (ИСПРАВЛЕНО)
# ==========================================

@router.callback_query(F.data == "skip_photo", AddBookWizard.photo)
async def skip_photo(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Фото пропущено")
    await process_photo_step_complete(callback.message, state)

@router.message(AddBookWizard.photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    """
    Обработка загруженного фото.
    Здесь была основная ошибка с пустым файлом.
    """
    photo = message.photo[-1] # Берем самое качественное фото

    if photo.file_size > 5 * 1024 * 1024:
        await message.answer(
            "❌ Файл слишком большой (максимум 5 МБ)\n"
            "Попробуйте загрузить другое фото или пропустите этот шаг.",
            parse_mode="HTML"
        )
        return

    try:
        # 1. Скачиваем фото в объект BytesIO (в память)
        # Если destination не указан, aiogram создает BytesIO
        file_io = await message.bot.download(photo.file_id)

        # 2. ИСПРАВЛЕНИЕ ОШИБКИ:
        # После скачивания курсор находится в конце файла.
        # .read() вернет пустоту. Нужно использовать .getvalue() или .seek(0).
        if isinstance(file_io, io.BytesIO):
            photo_bytes = file_io.getvalue()
        else:
            # На случай изменений в aiogram, если вернется не BytesIO
            file_io.seek(0)
            photo_bytes = file_io.read()

        if not photo_bytes:
            raise ValueError("Получен пустой файл")

        # 3. Сохраняем БАЙТЫ в состояние визарда
        data = await state.get_data()
        wizard_data = data.get("wizard_data", {})
        wizard_data["photo_bytes"] = photo_bytes
        # Ставим флаг, что фото есть (путь будет создан на сервере)
        wizard_data["has_photo"] = True

        await state.update_data(wizard_data=wizard_data)

        await message.answer("✅ Фото обложки сохранено в черновик!")
        await process_photo_step_complete(message, state)

    except Exception as e:
        print(f"Error processing photo: {e}")
        await message.answer(
            "❌ Не удалось обработать фото. Попробуйте еще раз или пропустите этот шаг.",
            parse_mode="HTML"
        )

@router.message(AddBookWizard.photo)
async def process_document_as_photo(message: Message):
    await message.answer("⚠️ Пожалуйста, отправьте изображение как <b>Фото</b> (со сжатием), а не как Файл.", parse_mode="HTML")


async def process_photo_step_complete(message: Message, state: FSMContext):
    """Переход к выбору жанра/описания"""
    await state.set_state(AddBookWizard.description)

    try:
        # Получаем жанры из API
        genres_resp = await api.get_genres()
        # Если API возвращает словарь {"genres": [...]}, берем список, иначе считаем списком
        if isinstance(genres_resp, dict):
            genres = genres_resp.get("genres", [])
        else:
            genres = genres_resp

        builder = InlineKeyboardBuilder()

        # Популярные жанры для быстрого выбора
        common_genres = ["Роман", "Фантастика", "Non-fiction", "Бизнес", "Психология"]

        # Фильтруем, чтобы показать только те, что есть в системе, или дефолтные
        display_genres = [g for g in common_genres if g in genres] if genres else common_genres

        for genre in display_genres:
            builder.row(InlineKeyboardButton(text=f"📚 {genre}", callback_data=f"select_genre:{genre}"))

        builder.row(InlineKeyboardButton(text="✏️ Ввести свой жанр/описание", callback_data="select_genre:custom"))
        builder.row(InlineKeyboardButton(text="⏭ Пропустить описание", callback_data="skip_description"))
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="wizard_cancel"))

        await message.answer(
            f"📝 <b>Шаг 4/6:</b> Описание и жанр\n\n"
            f"Выберите жанр из списка или введите описание вручную:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"Error getting genres: {e}")
        # Фолбек если API недоступно
        await message.answer(
            f"📝 <b>Шаг 4/6:</b> Описание и жанр\n\n"
            f"Введите жанр или краткое описание книги:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )


# ==========================================
# 5. ОПИСАНИЕ И ЖАНР
# ==========================================

@router.callback_query(F.data == "skip_description", AddBookWizard.description)
async def skip_description(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Описание пропущено")
    await process_description_complete(callback.message, state)

@router.callback_query(F.data.startswith("select_genre:"), AddBookWizard.description)
async def select_genre_callback(callback: CallbackQuery, state: FSMContext):
    genre = callback.data.split(":")[1]

    if genre == "custom":
        await callback.message.edit_text(
            "✍️ Введите описание или свой жанр текстом:",
            reply_markup=cancel_keyboard()
        )
        return

    # Сохраняем выбранный жанр
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["genre"] = genre
    wizard_data["description"] = f"Жанр: {genre}" # Дефолтное описание
    await state.update_data(wizard_data=wizard_data)

    await callback.answer(f"Выбран жанр: {genre}")
    await process_description_complete(callback.message, state)

@router.message(AddBookWizard.description)
async def process_description(message: Message, state: FSMContext):
    text = message.text.strip()
    is_valid, error_msg = validate_description(text)
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return

    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["description"] = text
    # Если жанр не был выбран кнопкой, ставим "Другое" или пытаемся определить
    if "genre" not in wizard_data:
        wizard_data["genre"] = "Другое"

    await state.update_data(wizard_data=wizard_data)
    await message.answer("✅ Описание сохранено")
    await process_description_complete(message, state)


async def process_description_complete(message: Message, state: FSMContext):
    """Переход к выбору владельца"""
    await state.set_state(AddBookWizard.owner)
    try:
        users = await api.search_users()
        if not users:
            await message.answer(
                "📝 <b>Шаг 5/6:</b> Владелец книги\n\n"
                "Введите username или имя владельца для поиска:",
                reply_markup=cancel_keyboard(),
                parse_mode="HTML"
            )
            return

        # Сохраняем всех пользователей в стейт для быстрого поиска по ID
        await state.update_data(all_users=users)

        await message.answer(
            f"📝 <b>Шаг 5/6:</b> Владелец книги\n\n"
            f"Выберите владельца книги из списка или введите имя для поиска:",
            reply_markup=users_selection_keyboard(users),
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer("❌ Ошибка загрузки списка пользователей. Введите имя вручную.")
        print(f"Error loading users: {e}")


# ==========================================
# 6. ВЛАДЕЛЕЦ
# ==========================================

@router.message(AddBookWizard.owner)
async def search_owner(message: Message, state: FSMContext):
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("❌ Введите минимум 2 символа для поиска.", parse_mode="HTML")
        return
    try:
        users = await api.search_users(query)
        if not users:
            await message.answer(f"🔍 Никого не нашли по запросу «{query}».", parse_mode="HTML")
            return

        # Обновляем кэш пользователей
        await state.update_data(all_users=users)
        await message.answer(f"🔍 Результаты поиска:", reply_markup=users_selection_keyboard(users), parse_mode="HTML")
    except Exception as e:
        print(f"Error searching users: {e}")
        await message.answer("❌ Ошибка поиска")

@router.callback_query(F.data.startswith("select_owner:"), AddBookWizard.owner)
async def select_owner(callback: CallbackQuery, state: FSMContext):
    owner_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    all_users = data.get("all_users", [])

    # Ищем пользователя в сохраненном списке
    owner = next((u for u in all_users if u["id"] == owner_id), None)

    if not owner:
        # Если не нашли в кэше, пробуем запросить API
        try:
            users = await api.search_users()
            owner = next((u for u in users if u["id"] == owner_id), None)
        except: pass

    if not owner:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    owner_name = owner.get("username") or owner.get("full_name", "Неизвестный")

    wizard_data = data.get("wizard_data", {})
    wizard_data["owner_id"] = owner_id
    wizard_data["owner_name"] = owner_name
    await state.update_data(wizard_data=wizard_data)

    await callback.answer(f"Выбран: {owner_name}")
    await process_owner_selected(callback.message, state)


# ==========================================
# 7. ПОДТВЕРЖДЕНИЕ И СОЗДАНИЕ
# ==========================================

async def process_owner_selected(message: Message, state: FSMContext):
    """
    Шаг 6: Предпросмотр и подтверждение
    """
    await state.set_state(AddBookWizard.confirm)
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})

    preview_text = format_wizard_preview(wizard_data)

    # Проверка наличия фото
    photo_bytes = wizard_data.get("photo_bytes")

    if photo_bytes:
        # Если есть фото, отправляем его с подписью
        input_file = BufferedInputFile(photo_bytes, filename="preview.jpg")
        await message.answer_photo(
            photo=input_file,
            caption=preview_text,
            reply_markup=wizard_confirm_keyboard(),
            parse_mode="HTML"
        )
    else:
        # Если фото нет, просто текст
        await message.answer(
            preview_text,
            reply_markup=wizard_confirm_keyboard(),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "wizard_confirm", AddBookWizard.confirm)
async def confirm_and_create_book(callback: CallbackQuery, state: FSMContext):
    """
    Финальное создание книги
    """
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    photo_bytes = wizard_data.get("photo_bytes")

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏳ Создаю книгу, подождите...")

    try:
        book_fields = {
            "title": wizard_data["title"],
            "author": wizard_data["author"],
            "owner_id": wizard_data["owner_id"],
            "description": wizard_data.get("description"),
            "genre": wizard_data.get("genre", "Другое")
        }

        # Отправляем в API
        # ВАЖНО: api.create_book должен поддерживать аргумент photo_bytes
        book = await api.create_book(book_fields, photo_bytes=photo_bytes)

        if not book:
            raise Exception("API вернуло пустой ответ")

        await state.clear()

        result_text = (
            f"✅ <b>Книга успешно добавлена!</b>\n\n"
            f"📖 {book.get('title', 'Без названия')}\n"
            f"🔖 ID: #{book.get('id', '???')}"
        )

        is_admin = callback.from_user.id in settings.admin_ids_list
        await callback.message.answer(
            result_text,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(is_admin)
        )

    except Exception as e:
        await callback.answer("Ошибка создания книги", show_alert=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)}")
        print(f"Error creating book: {e}")


@router.callback_query(F.data == "wizard_cancel")
async def cancel_wizard(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_list

    await callback.message.delete()
    await callback.message.answer(
        "❌ Добавление отменено",
        reply_markup=main_menu_keyboard(is_admin)
    )