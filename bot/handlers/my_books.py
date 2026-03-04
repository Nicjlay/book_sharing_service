"""
Handlers для «Мои книги», «Книги на руках», истории и редактирования книг
"""
import asyncio
import io
import logging
from datetime import datetime
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api.client import APIError, api
from config import settings
from keyboards.inline import (
    book_card_keyboard,
    borrowed_book_actions_keyboard,
    borrowed_books_keyboard,
    cancel_keyboard,
    edit_field_keyboard,
    main_menu_keyboard,
)
from states.wizard import EditBookStates
from utils.formatters import escape_html, format_book_card, format_history, format_my_books
from utils.telegram import safe_delete_message, safe_edit_message
from utils.validators import validate_author, validate_description, validate_title

logger = logging.getLogger(__name__)
router = Router()

_STATUS_EMOJI = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}
_MY_BOOKS_BTN_LIMIT = 10


@router.message(Command("mybooks"))
@router.callback_query(F.data == "my_books")
async def show_my_books(event: Message | CallbackQuery, state: FSMContext):
    """Показать мои книги (владелец или заёмщик)"""
    await state.clear()
    user_id = event.from_user.id
    is_admin = user_id in settings.admin_ids_set

    try:
        books = await api.get_books(user_id=user_id)

        if not books:
            text = (
                "📚 <b>Мои книги</b>\n\n"
                "У вас пока нет книг.\n\n"
                "💡 Вы можете:\n"
                "• Забронировать книгу из каталога\n"
                "• Попросить админа добавить вашу книгу"
            )
            keyboard = main_menu_keyboard(is_admin)
            if isinstance(event, CallbackQuery):
                await safe_edit_message(event.message, text, reply_markup=keyboard)
                await event.answer()
            else:
                await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
            return

        text = format_my_books(books, user_id)

        builder = InlineKeyboardBuilder()
        owned = [b for b in books if b.get("owner_id") == user_id]
        borrowed = [b for b in books if b.get("borrower_id") == user_id]

        if owned:
            for book in owned[:_MY_BOOKS_BTN_LIMIT]:
                emoji = _STATUS_EMOJI.get(book.get("status"), "⚪️")
                # FIX: book.get("title", "—") вместо book["title"] — KeyError если title отсутствует
                raw_title = book.get("title", "—")
                title = raw_title[:28] + ".." if len(raw_title) > 28 else raw_title
                book_id = book.get("id", 0)
                builder.row(InlineKeyboardButton(
                    text=f"{emoji} {title}",
                    callback_data=f"book:{book_id}"
                ))

            extra = len(owned) - _MY_BOOKS_BTN_LIMIT
            if extra > 0:
                builder.row(InlineKeyboardButton(
                    text=f"📖 Ещё {extra} книг — смотрите в каталоге",
                    callback_data="catalog"
                ))

        if borrowed:
            builder.row(InlineKeyboardButton(
                text=f"📖 Книги на руках ({len(borrowed)})",
                callback_data="my_borrowed"
            ))

        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))

        if isinstance(event, CallbackQuery):
            await safe_edit_message(event.message, text, reply_markup=builder.as_markup())
            await event.answer()
        else:
            await event.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

    except Exception as e:
        text = "❌ Ошибка загрузки книг"
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        logger.error("Error loading my books for user %d: %s", event.from_user.id, e)


@router.callback_query(F.data == "my_borrowed")
async def show_borrowed_books(callback: CallbackQuery):
    """Список книг, которые пользователь взял"""
    user_id = callback.from_user.id

    try:
        borrowed_books, reserved_books = await _fetch_active_books(user_id)
        borrowed = borrowed_books + reserved_books

        if not borrowed:
            await safe_edit_message(
                callback.message,
                "📖 <b>Книги на руках</b>\n\n"
                "У вас нет книг на руках.\n\n"
                "Перейдите в каталог, чтобы забронировать книгу.",
                reply_markup=main_menu_keyboard(user_id in settings.admin_ids_set)
            )
            await callback.answer()
            return

        status_label = {
            "borrowed": "🔴 Выдана",
            "overdue": "⏳ Просрочена",
            "reserved": "🟡 Ожидает выдачи",
        }
        text = "📖 <b>Книги на руках:</b>\n\n"
        for book in borrowed:
            label = status_label.get(book.get("status"), "📖")
            due = ""
            if book.get("return_due_date"):
                try:
                    d = datetime.fromisoformat(book["return_due_date"].replace("Z", "+00:00"))
                    due = f" · до {d.strftime('%d.%m.%Y')}"
                except (ValueError, TypeError) as e:
                    # FIX: логируем ошибку парсинга даты вместо silent pass.
                    # except Exception: pass скрывает баги типа AttributeError
                    # (если return_due_date — не строка, а dict/None).
                    logger.warning(
                        "Invalid return_due_date %r for book %s in show_borrowed_books: %s",
                        book.get("return_due_date"), book.get("id"), e,
                    )
            safe_title = escape_html(book.get("title", "—"))
            safe_author = escape_html(book.get("author", "—"))
            text += f"{label}{due}\n<b>{safe_title}</b>\n✍️ {safe_author}\n\n"

        text += "👇 Выберите книгу для возврата:"

        await safe_edit_message(callback.message, text, reply_markup=borrowed_books_keyboard(borrowed))
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки", show_alert=True)
        logger.error("Error loading borrowed books for user %d: %s", user_id, e)


async def _fetch_active_books(user_id: int) -> tuple[list, list]:
    """
    Загружает книги на руках через три параллельных запроса к API:
    borrowed, overdue и reserved.

    Исправление: исходная версия не запрашивала overdue-книги, из-за чего
    пользователи с просроченными книгами не видели их в разделе «На руках».
    Теперь borrowed + overdue объединяются в первый элемент кортежа.

    return_exceptions=True позволяет обработать частичный успех —
    если один статус недоступен, остальные отображаются корректно.
    """
    borrowed_result, overdue_result, reserved_result = await asyncio.gather(
        api.get_books(user_id=user_id, status="borrowed"),
        api.get_books(user_id=user_id, status="overdue"),
        api.get_books(user_id=user_id, status="reserved"),
        return_exceptions=True,
    )

    active_borrowed: list = []
    reserved: list = []

    if isinstance(borrowed_result, Exception):
        logger.error("Failed to fetch borrowed books for user %d: %s", user_id, borrowed_result)
    elif isinstance(borrowed_result, list):
        active_borrowed += [b for b in borrowed_result if b.get("borrower_id") == user_id]

    if isinstance(overdue_result, Exception):
        logger.error("Failed to fetch overdue books for user %d: %s", user_id, overdue_result)
    elif isinstance(overdue_result, list):
        active_borrowed += [b for b in overdue_result if b.get("borrower_id") == user_id]

    if isinstance(reserved_result, Exception):
        logger.error("Failed to fetch reserved books for user %d: %s", user_id, reserved_result)
    elif isinstance(reserved_result, list):
        reserved = [b for b in reserved_result if b.get("borrower_id") == user_id]

    return active_borrowed, reserved


@router.callback_query(F.data.startswith("borrowed_detail:"))
async def show_borrowed_book_detail(callback: CallbackQuery):
    """Карточка конкретной книги на руках"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return

    try:
        book = await api.get_book(book_id)
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        text = format_book_card(book)

        if book.get("image_path"):
            try:
                photo_bytes = await api.get_image_bytes(book["image_path"])
                if photo_bytes:
                    photo = BufferedInputFile(photo_bytes, filename="cover.jpg")
                    await safe_delete_message(callback.message)
                    await callback.message.answer_photo(
                        photo=photo,
                        caption=text,
                        reply_markup=borrowed_book_actions_keyboard(book_id),
                        parse_mode="HTML",
                    )
                    await callback.answer()
                    return
            except Exception as e:
                logger.warning("Photo error for borrowed_detail %d: %s", book_id, e)

        await safe_edit_message(
            callback.message, text,
            reply_markup=borrowed_book_actions_keyboard(book_id),
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки книги", show_alert=True)
        logger.error("Error in borrowed_detail (book %d): %s", book_id, e)


@router.callback_query(F.data.startswith("history:"))
async def show_book_history(callback: CallbackQuery):
    """Показать историю книги"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return

    try:
        history = await api.get_book_history(book_id)

        if not history:
            await callback.answer("История пуста", show_alert=True)
            return

        text = format_history(history)

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 К книге", callback_data=f"book:{book_id}"))

        await safe_edit_message(callback.message, text, reply_markup=builder.as_markup())
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки истории", show_alert=True)
        logger.error("Error loading history for book %d: %s", book_id, e)


@router.callback_query(F.data.startswith("delete:"))
async def confirm_delete_book(callback: CallbackQuery):
    """Подтверждение удаления книги"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return

    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_set

    try:
        book = await api.get_book(book_id)
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        if book.get("owner_id") != user_id and not is_admin:
            await callback.answer("Только владелец может удалить книгу", show_alert=True)
            return

        if book.get("status") in ("borrowed", "reserved"):
            await callback.answer(
                "Нельзя удалить книгу, которая забронирована или выдана",
                show_alert=True,
            )
            return

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{book_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"book:{book_id}")
        )
        safe_title = escape_html(book.get("title", "—"))
        safe_author = escape_html(book.get("author", "—"))
        await safe_edit_message(
            callback.message,
            f"❓ <b>Подтверждение удаления</b>\n\n"
            f"Вы уверены, что хотите удалить книгу?\n\n"
            f"📖 {safe_title}\n✍️ {safe_author}\n\n"
            f"⚠️ Это действие нельзя отменить!",
            reply_markup=builder.as_markup(),
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка", show_alert=True)
        logger.error("Error in confirm_delete (book %d): %s", book_id, e)


@router.callback_query(F.data.startswith("confirm_delete:"))
async def delete_book(callback: CallbackQuery):
    """Окончательное удаление книги"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return

    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_set

    try:
        await api.delete_book(book_id, user_id)
        await safe_delete_message(callback.message)
        await callback.message.answer(
            "✅ <b>Книга удалена</b>\n\nКнига перемещена в архив.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )
        await callback.answer("Удалено")

    except APIError as e:
        if e.status == 403:
            await callback.answer("Нет прав на удаление этой книги", show_alert=True)
        elif e.status == 400:
            await callback.answer("Нельзя удалить книгу в текущем статусе", show_alert=True)
        elif e.status == 404:
            await callback.answer("Книга уже удалена", show_alert=True)
        else:
            await callback.answer("Ошибка удаления", show_alert=True)
        logger.error("Error deleting book %d: HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await callback.answer("Ошибка удаления", show_alert=True)
        logger.error("Error deleting book %d: %s", book_id, e)


# ---------------------------------------------------------------------------
# РЕДАКТИРОВАНИЕ КНИГИ
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:"))
async def start_edit_book(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования книги"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return

    user_id = callback.from_user.id

    try:
        book = await api.get_book(book_id)
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return
        if book.get("owner_id") != user_id and user_id not in settings.admin_ids_set:
            await callback.answer(
                "Только владелец или администратор может редактировать книгу",
                show_alert=True,
            )
            return

        await state.update_data(edit_book_id=book_id)
        await state.set_state(EditBookStates.select_field)

        safe_title = escape_html(book.get("title", "—"))
        await safe_edit_message(
            callback.message,
            f"✏️ <b>Редактирование книги</b>\n\n"
            f"📖 {safe_title}\n\n"
            f"Выберите поле для изменения:",
            reply_markup=edit_field_keyboard(),
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка", show_alert=True)
        logger.error("Error starting edit for book %d: %s", book_id, e)


@router.callback_query(F.data.startswith("edit_field:"), EditBookStates.select_field)
async def process_edit_field_select(callback: CallbackQuery, state: FSMContext):
    """Выбор поля для редактирования"""
    field = callback.data.split(":", 1)[1]

    prompts = {
        "author": ("✍️ Введите нового автора (фамилия имя):", EditBookStates.edit_author),
        "title": ("📖 Введите новое название книги:", EditBookStates.edit_title),
        "description": ("📝 Введите новое описание (или «-» чтобы удалить):", EditBookStates.edit_description),
        "genre": ("📚 Введите новый жанр (или «-» чтобы удалить):", EditBookStates.edit_genre),
    }

    if field == "photo":
        await state.set_state(EditBookStates.edit_photo)
        await safe_edit_message(
            callback.message,
            "🖼 Отправьте новое фото обложки как <b>изображение</b> (JPG/PNG/WebP, до 5 МБ):",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if field not in prompts:
        await callback.answer("Неизвестное поле", show_alert=True)
        return

    prompt_text, new_state = prompts[field]
    await state.set_state(new_state)
    await safe_edit_message(callback.message, prompt_text, reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(EditBookStates.edit_author)
async def save_edit_author(message: Message, state: FSMContext):
    author = message.text.strip() if message.text else ""
    is_valid, error_msg = validate_author(author)
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return
    await _apply_edit(message, state, {"author": author}, f"Автор обновлён: <b>{escape_html(author)}</b>")


@router.message(EditBookStates.edit_title)
async def save_edit_title(message: Message, state: FSMContext):
    title = message.text.strip() if message.text else ""
    is_valid, error_msg = validate_title(title)
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return
    await _apply_edit(message, state, {"title": title}, f"Название обновлено: <b>{escape_html(title)}</b>")


@router.message(EditBookStates.edit_description)
async def save_edit_description(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    new_desc = "" if text == "-" else text
    is_valid, error_msg = validate_description(new_desc)
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return
    label = "Описание удалено" if text == "-" else "Описание обновлено"
    await _apply_edit(message, state, {"description": new_desc}, f"✅ {label}")


@router.message(EditBookStates.edit_genre)
async def save_edit_genre(message: Message, state: FSMContext):
    genre = message.text.strip() if message.text else ""
    if genre != "-" and len(genre) > 50:
        await message.answer("❌ Название жанра слишком длинное (максимум 50 символов).")
        return
    new_genre = "" if genre == "-" else genre
    label = "Жанр удалён" if genre == "-" else f"Жанр обновлён: <b>{escape_html(new_genre)}</b>"
    await _apply_edit(message, state, {"genre": new_genre}, f"✅ {label}")


@router.message(EditBookStates.edit_photo, F.photo)
async def save_edit_photo(message: Message, state: FSMContext):
    """Сохранение нового фото"""
    photo = message.photo[-1]

    if photo.file_size > 5 * 1024 * 1024:
        await message.answer("❌ Файл слишком большой (максимум 5 МБ)")
        return

    data = await state.get_data()
    book_id = data.get("edit_book_id")
    user_id = message.from_user.id

    if not book_id:
        await state.clear()
        await message.answer("❌ Сессия устарела. Начните редактирование заново.")
        return

    try:
        file_io = await message.bot.download(photo.file_id)
        photo_bytes = file_io.getvalue() if isinstance(file_io, io.BytesIO) else file_io.read()

        if not photo_bytes:
            raise ValueError("Получен пустой файл")

        # Сначала загружаем медиа, потом обновляем книгу.
        # Если update_book упадёт — файл останется в хранилище (orphan),
        # но книга не будет в рассинхроне. Это лучше, чем обратный порядок.
        media_result = await api.upload_media(photo_bytes, filename="cover.jpg")
        image_path = media_result.get("path") or media_result.get("image_path") or media_result.get("url")

        if not image_path:
            raise ValueError("API не вернуло путь к изображению")

        await api.update_book(book_id, user_id, {"image_path": image_path})
        await state.clear()

        is_admin = user_id in settings.admin_ids_set
        await message.answer(
            "✅ <b>Фото обновлено!</b>",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )
    except APIError as e:
        await message.answer(f"❌ Ошибка загрузки фото (HTTP {e.status}). Попробуйте ещё раз.")
        logger.error("Error saving edit photo for book %d: HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await message.answer("❌ Ошибка загрузки фото. Попробуйте ещё раз.")
        logger.error("Error saving edit photo: %s", e)


@router.message(EditBookStates.edit_photo)
async def invalid_edit_photo(message: Message):
    await message.answer(
        "❌ Пожалуйста, отправьте <b>изображение</b> (JPG/PNG/WebP).",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


async def _apply_edit(message: Message, state: FSMContext, update_data: dict, success_text: str):
    """Применяет изменение через API и показывает результат."""
    data = await state.get_data()
    book_id = data.get("edit_book_id")
    user_id = message.from_user.id

    if not book_id:
        await state.clear()
        await message.answer("❌ Сессия устарела. Начните редактирование заново.")
        return

    loading = await message.answer("⏳ Сохраняем изменения...")
    try:
        await api.update_book(book_id, user_id, update_data)
        await state.clear()
        await safe_delete_message(loading)

        is_admin = user_id in settings.admin_ids_set
        await message.answer(
            f"✅ {success_text}\n\nИзменения сохранены.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )
    except APIError as e:
        await safe_delete_message(loading)
        await state.clear()
        if e.status == 403:
            text = "❌ Нет прав на редактирование этой книги."
        elif e.status == 422:
            text = "❌ Некорректные данные. Проверьте введённые значения."
        elif e.status == 404:
            text = "❌ Книга не найдена."
        else:
            text = "❌ Ошибка сохранения. Попробуйте ещё раз."
        await message.answer(text, parse_mode="HTML")
        logger.error("Error applying edit to book %d: HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await safe_delete_message(loading)
        await state.clear()
        await message.answer("❌ Ошибка сохранения. Попробуйте ещё раз.", parse_mode="HTML")
        logger.error("Error applying edit to book %d: %s", book_id, e)
