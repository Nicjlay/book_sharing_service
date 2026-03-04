"""
Handlers для бронирования и возврата книг (ТЗ 3.2-3.3)
"""
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from api.client import APIError, api
from config import settings
from keyboards.inline import (
    cancel_keyboard,
    main_menu_keyboard,
    reservation_days_keyboard,
    return_photo_keyboard,
)
from states.wizard import ReservationStates, ReturnBookStates
from utils.formatters import escape_html
from utils.telegram import safe_delete_message, safe_edit_message
from utils.validators import validate_days

logger = logging.getLogger(__name__)
router = Router()


async def _go_home(message: Message, user_id: int, text: str):
    """Удаляет текущее сообщение и показывает главное меню с результатом."""
    is_admin = user_id in settings.admin_ids_set
    await safe_delete_message(message)
    await message.answer(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")


def _format_reservation_error(e: APIError) -> str:
    """
    Формирует UX-сообщение для API-ошибок бронирования.

    409 может означать как «книга занята», так и «превышен лимит книг».
    Различаем их по ключевым словам в detail-сообщении API.
    """
    if e.status == 409:
        msg = e.message.lower()
        _LIMIT_KEYWORDS = (
            "5 книг", "более 5", "limit", "лимит", "превышен",
            "максимум", "maximum", "exceed",
        )
        if any(kw in msg for kw in _LIMIT_KEYWORDS):
            return (
                "❌ <b>Превышен лимит книг</b>\n\n"
                "Нельзя иметь более 5 книг одновременно.\n"
                "Верните уже взятые книги."
            )
        return (
            "❌ <b>Книга уже забронирована кем-то другим.</b>\n\n"
            "Вы можете встать в лист ожидания через каталог."
        )
    if e.status == 400:
        return "❌ Книга недоступна для бронирования."
    if e.status == 403:
        return "❌ Нельзя забронировать эту книгу."
    return "❌ Ошибка бронирования. Попробуйте позже."


def _make_success_text(days: int) -> str:
    """Формирует текст успешного бронирования."""
    due_date = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%d.%m.%Y")
    return (
        f"✅ <b>Запрос на бронирование отправлен!</b>\n\n"
        f"📅 Срок: {days} дней (до {due_date})\n\n"
        f"⏳ Ожидайте подтверждения от администратора.\n"
        f"Вы получите уведомление, когда заявка будет обработана."
    )


# ---------------------------------------------------------------------------
# БРОНИРОВАНИЕ
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("reserve:"))
async def start_reservation(callback: CallbackQuery, state: FSMContext):
    """Начало бронирования книги (ТЗ 3.2.2)"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID книги", show_alert=True)
        return

    user_id = callback.from_user.id

    try:
        book = await api.get_book(book_id)
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        # FIX: book.get("status") вместо book["status"] — KeyError если поле отсутствует.
        # TOCTOU-смягчение: проверяем статус для быстрого UX-фидбека.
        # Окончательную проверку выполняет API при request_reservation.
        book_status = book.get("status")
        if book_status != "available":
            await callback.answer("Книга уже занята", show_alert=True)
            return

        if book.get("owner_id") == user_id:
            await callback.answer("Нельзя забронировать собственную книгу", show_alert=True)
            return

        await state.update_data(reserve_book_id=book_id)
        await state.set_state(ReservationStates.select_days)

        safe_title = escape_html(book.get("title", "—"))
        safe_author = escape_html(book.get("author", "—"))

        await safe_edit_message(
            callback.message,
            f"📖 <b>{safe_title}</b>\n"
            f"✍️ {safe_author}\n\n"
            f"📅 <b>Выберите срок бронирования:</b>\n\n"
            f"На какой срок вы хотите взять книгу? (1–90 дней)",
            reply_markup=reservation_days_keyboard(),
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка бронирования", show_alert=True)
        logger.error("Error in start_reservation (book %d): %s", book_id, e)


@router.callback_query(F.data.startswith("days:"), ReservationStates.select_days)
async def select_reservation_days(callback: CallbackQuery, state: FSMContext):
    """Выбор срока бронирования"""
    days_str = callback.data.split(":", 1)[1]

    if days_str == "custom":
        await safe_edit_message(
            callback.message,
            "✏️ Введите количество дней (1–90):",
            reply_markup=cancel_keyboard(),
        )
        await state.set_state(ReservationStates.custom_days)
        await callback.answer()
        return

    try:
        days = int(days_str)
    except ValueError:
        await callback.answer("Некорректное значение", show_alert=True)
        return

    await _process_reservation(callback, state, days)


@router.message(ReservationStates.custom_days)
async def process_custom_days(message: Message, state: FSMContext):
    """Обработка ввода произвольного срока"""
    is_valid, days, error_msg = validate_days(message.text or "")

    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return

    data = await state.get_data()
    book_id = data.get("reserve_book_id")
    user_id = message.from_user.id

    if not book_id:
        await state.clear()
        await message.answer(
            "❌ Сессия устарела. Начните бронирование заново.",
            reply_markup=main_menu_keyboard(user_id in settings.admin_ids_set),
        )
        return

    loading = await message.answer("⏳ Отправляем запрос...")

    try:
        await api.request_reservation(book_id, user_id, days)
        await state.clear()
        await safe_delete_message(loading)

        is_admin = user_id in settings.admin_ids_set
        await message.answer(
            _make_success_text(days),
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )

    except APIError as e:
        await safe_delete_message(loading)
        await state.clear()
        is_admin = user_id in settings.admin_ids_set
        await message.answer(
            _format_reservation_error(e),
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )
        logger.error(
            "Error in reservation (custom days, book %d): HTTP %d %s",
            book_id, e.status, e.message,
        )
    except Exception as e:
        await safe_delete_message(loading)
        await state.clear()
        is_admin = user_id in settings.admin_ids_set
        await message.answer(
            "❌ Ошибка бронирования. Попробуйте позже.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )
        logger.error("Error in reservation (custom days, book %d): %s", book_id, e)


async def _process_reservation(callback: CallbackQuery, state: FSMContext, days: int):
    """Отправка запроса на бронирование в API (для callback-пути)."""
    data = await state.get_data()
    book_id = data.get("reserve_book_id")
    user_id = callback.from_user.id

    if not book_id:
        await state.clear()
        await callback.answer("Сессия устарела, начните заново", show_alert=True)
        return

    await safe_edit_message(callback.message, "⏳ Отправляем запрос...")
    await callback.answer()

    try:
        await api.request_reservation(book_id, user_id, days)
        await state.clear()
        await _go_home(callback.message, user_id, _make_success_text(days))

    except APIError as e:
        await state.clear()
        await _go_home(callback.message, user_id, _format_reservation_error(e))
        logger.error("Error in reservation (book %d): HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await state.clear()
        await _go_home(callback.message, user_id, "❌ Ошибка бронирования. Попробуйте позже.")
        logger.error("Error in reservation (book %d): %s", book_id, e)


# ---------------------------------------------------------------------------
# WAITLIST
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("waitlist:"))
async def join_waitlist(callback: CallbackQuery):
    """Добавление в лист ожидания"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return

    user_id = callback.from_user.id
    await callback.answer("⏳ Добавляем в список...")

    try:
        result = await api.join_waitlist(book_id, user_id)
        already_in = isinstance(result, dict) and result.get("added") is False

        if already_in:
            await _go_home(
                callback.message, user_id,
                "ℹ️ <b>Вы уже в листе ожидания</b>\n\n"
                "Когда книга освободится, вы получите уведомление.",
            )
        else:
            await _go_home(
                callback.message, user_id,
                "🔔 <b>Вы добавлены в лист ожидания!</b>\n\n"
                "Когда книга освободится, вы получите уведомление.\n\n"
                "💡 Успейте первым забронировать её!",
            )

    except APIError as e:
        if e.status == 400:
            msg = e.message.lower()
            if "свою" in msg or "owner" in msg:
                text = "❌ Нельзя встать в очередь на свою книгу."
            elif "держите" in msg or "borrower" in msg:
                text = "❌ Нельзя встать в очередь на книгу, которую держите."
            else:
                text = "❌ Ошибка добавления в лист ожидания."
            await _go_home(callback.message, user_id, text)
        else:
            await callback.answer("Ошибка добавления в лист ожидания", show_alert=True)
        logger.error("Error joining waitlist (book %d): HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await callback.answer("Ошибка добавления в лист ожидания", show_alert=True)
        logger.error("Error joining waitlist (book %d): %s", book_id, e)


@router.callback_query(F.data.startswith("leave_waitlist:"))
async def leave_waitlist(callback: CallbackQuery):
    """Покинуть лист ожидания"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return

    user_id = callback.from_user.id

    try:
        await api.leave_waitlist(book_id, user_id)
        await _go_home(callback.message, user_id, "✅ Вы покинули лист ожидания.")
    except Exception as e:
        await callback.answer("Ошибка", show_alert=True)
        logger.error("Error leaving waitlist (book %d): %s", book_id, e)


# ---------------------------------------------------------------------------
# ВОЗВРАТ КНИГИ
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("return:"))
async def start_return_book(callback: CallbackQuery, state: FSMContext):
    """Начало процесса возврата книги (ТЗ 3.3)"""
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

        is_borrower = book.get("borrower_id") == user_id
        is_owner = book.get("owner_id") == user_id

        if not (is_borrower or is_owner or is_admin):
            await callback.answer("У вас нет прав на возврат этой книги", show_alert=True)
            return

        await state.update_data(return_book_id=book_id)
        await state.set_state(ReturnBookStates.upload_photo)

        await safe_delete_message(callback.message)

        safe_title = escape_html(book.get("title", "—"))
        await callback.message.answer(
            f"📖 <b>{safe_title}</b>\n\n"
            f"📸 <b>Возврат книги</b>\n\n"
            f"Загрузите фотографию книги для подтверждения возврата.\n"
            f"Это поможет владельцу проверить состояние.\n\n"
            f"Или нажмите «Пропустить».",
            reply_markup=return_photo_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка возврата", show_alert=True)
        logger.error("Error in start_return (book %d): %s", book_id, e)


@router.callback_query(F.data == "skip_return_photo", ReturnBookStates.upload_photo)
async def skip_return_photo(callback: CallbackQuery, state: FSMContext):
    """Пропуск фото при возврате"""
    await callback.answer()
    await _do_return(callback.message, callback.from_user.id, state, photo_data=None)


@router.message(ReturnBookStates.upload_photo, F.photo)
async def process_return_photo(message: Message, state: FSMContext):
    """Обработка фото при возврате"""
    photo = message.photo[-1]
    try:
        file = await message.bot.download(photo.file_id)
        photo_data = file.getvalue() if isinstance(file, io.BytesIO) else file.read()
        loading = await message.answer("⏳ Обрабатываем возврат...")
        await _do_return(loading, message.from_user.id, state, photo_data)
    except Exception as e:
        await message.answer("❌ Ошибка обработки фото. Попробуйте ещё раз.")
        logger.error("Error processing return photo: %s", e)


@router.message(ReturnBookStates.upload_photo)
async def invalid_return_photo(message: Message):
    """Неправильный формат фото"""
    await message.answer(
        "❌ Пожалуйста, отправьте <b>изображение</b> или нажмите «Пропустить»",
        reply_markup=return_photo_keyboard(),
        parse_mode="HTML",
    )


async def _do_return(
    message: Message,
    user_id: int,
    state: FSMContext,
    photo_data: Optional[bytes] = None,
):
    """Выполняет возврат книги через API."""
    data = await state.get_data()
    book_id = data.get("return_book_id")

    if not book_id:
        await state.clear()
        await _go_home(message, user_id, "❌ Сессия устарела. Начните возврат заново.")
        return

    try:
        await api.return_book(book_id, user_id, photo_bytes=photo_data)
        await state.clear()
        await _go_home(
            message, user_id,
            "✅ <b>Книга успешно возвращена!</b>\n\n"
            "Книга снова доступна в каталоге.\n"
            "Владелец получил уведомление о возврате.",
        )
    except APIError as e:
        await state.clear()
        if e.status == 403:
            text = "❌ Нет прав на возврат этой книги."
        elif e.status == 400:
            text = "❌ Книга не числится за вами или уже возвращена."
        elif e.status == 404:
            text = "❌ Книга не найдена."
        else:
            text = "❌ Ошибка возврата книги. Обратитесь к администратору."
        await _go_home(message, user_id, text)
        logger.error("Error returning book %d: HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await state.clear()
        await _go_home(message, user_id, "❌ Ошибка возврата книги. Обратитесь к администратору.")
        logger.error("Error returning book %d: %s", book_id, e)
