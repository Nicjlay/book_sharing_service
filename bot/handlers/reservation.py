"""
Handlers для бронирования и возврата книг (ТЗ 3.2-3.3)
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta

from api.client import api
from states.wizard import ReservationStates, ReturnBookStates
from keyboards.inline import (
    reservation_days_keyboard, return_photo_keyboard,
    book_card_keyboard, cancel_keyboard, main_menu_keyboard
)
from utils.validators import validate_days
from utils.telegram import safe_edit_message
from config import settings

router = Router()


async def _go_home(message: Message, user_id: int, text: str):
    """Удаляет текущее сообщение и показывает главное меню с результатом."""
    is_admin = user_id in settings.admin_ids_list
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")


# ---------------------------------------------------------------------------
# БРОНИРОВАНИЕ
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("reserve:"))
async def start_reservation(callback: CallbackQuery, state: FSMContext):
    """Начало бронирования книги (ТЗ 3.2.2)"""
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    try:
        book = await api.get_book(book_id)
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        if book["status"] != "available":
            await callback.answer("Книга уже занята", show_alert=True)
            return

        if book.get("owner_id") == user_id:
            await callback.answer("Нельзя забронировать собственную книгу", show_alert=True)
            return

        await state.update_data(reserve_book_id=book_id)
        await state.set_state(ReservationStates.select_days)

        await safe_edit_message(
            callback.message,
            f"📖 <b>{book['title']}</b>\n"
            f"✍️ {book['author']}\n\n"
            f"📅 <b>Выберите срок бронирования:</b>\n\n"
            f"На какой срок вы хотите взять книгу? (1–90 дней)",
            reply_markup=reservation_days_keyboard()
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка бронирования", show_alert=True)
        print(f"Error in start_reservation: {e}")


@router.callback_query(F.data.startswith("days:"), ReservationStates.select_days)
async def select_reservation_days(callback: CallbackQuery, state: FSMContext):
    """Выбор срока бронирования (ТЗ 3.2.2)"""
    days_str = callback.data.split(":")[1]

    if days_str == "custom":
        await safe_edit_message(
            callback.message,
            "✏️ Введите количество дней (1–90):",
            reply_markup=cancel_keyboard()
        )
        await state.set_state(ReservationStates.custom_days)
        await callback.answer()
        return

    days = int(days_str)
    await _process_reservation(callback, state, days)


@router.message(ReservationStates.custom_days)
async def process_custom_days(message: Message, state: FSMContext):
    """Обработка ввода произвольного срока"""
    # ИЗМЕНЕНИЕ v2: days ge=1, le=90 (валидатор уже проверяет это)
    is_valid, days, error_msg = validate_days(message.text)

    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return

    data = await state.get_data()
    book_id = data.get("reserve_book_id")
    user_id = message.from_user.id

    loading = await message.answer("⏳ Отправляем запрос...")

    try:
        await api.request_reservation(book_id, user_id, days)
        await state.clear()
        due_date = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")

        try:
            await loading.delete()
        except Exception:
            pass

        is_admin = user_id in settings.admin_ids_list
        await message.answer(
            f"✅ <b>Запрос на бронирование отправлен!</b>\n\n"
            f"📅 Желаемый срок: {days} дней (до {due_date})\n\n"
            f"⏳ Ожидайте подтверждения от администратора.\n"
            f"Вы получите уведомление когда заявка будет обработана.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML"
        )

    except Exception as e:
        try:
            await loading.delete()
        except Exception:
            pass

        error_msg = str(e)
        user_id = message.from_user.id
        is_admin = user_id in settings.admin_ids_list

        # ИЗМЕНЕНИЕ v2: новая 409 при превышении лимита одновременных книг
        if "409" in error_msg:
            if "5 книг" in error_msg or "более 5" in error_msg:
                text = (
                    "❌ <b>Превышен лимит книг</b>\n\n"
                    "Нельзя иметь более 5 книг одновременно.\n"
                    "Верните уже взятые книги."
                )
            else:
                text = (
                    "❌ <b>Книга уже забронирована</b>\n\n"
                    "Хотите встать в лист ожидания?"
                )
        else:
            text = "❌ Ошибка бронирования. Попробуйте позже."

        await message.answer(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
        print(f"Error in reservation: {e}")


async def _process_reservation(callback: CallbackQuery, state: FSMContext, days: int):
    """Отправка запроса на бронирование в API"""
    data = await state.get_data()
    book_id = data.get("reserve_book_id")
    user_id = callback.from_user.id

    await safe_edit_message(callback.message, "⏳ Отправляем запрос...")
    await callback.answer()

    try:
        await api.request_reservation(book_id, user_id, days)
        await state.clear()

        due_date = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")
        await _go_home(
            callback.message,
            user_id,
            f"✅ <b>Запрос на бронирование отправлен!</b>\n\n"
            f"📅 Срок: {days} дней (до {due_date})\n\n"
            f"⏳ Ожидайте подтверждения от администратора.\n"
            f"Вы получите уведомление, когда заявка будет обработана."
        )

    except Exception as e:
        error_msg = str(e)
        # ИЗМЕНЕНИЕ v2: новая 409 при превышении лимита одновременных книг (>5)
        if "409" in error_msg:
            if "5 книг" in error_msg or "более 5" in error_msg:
                text = (
                    "❌ <b>Превышен лимит книг</b>\n\n"
                    "Нельзя иметь более 5 книг одновременно.\n"
                    "Верните уже взятые книги."
                )
            else:
                text = (
                    "❌ <b>Книга уже забронирована кем-то другим.</b>\n\n"
                    "Вы можете встать в лист ожидания через каталог."
                )
        else:
            text = "❌ Ошибка бронирования. Попробуйте позже."

        await _go_home(callback.message, user_id, text)
        print(f"Error in reservation: {e}")


# ---------------------------------------------------------------------------
# WAITLIST
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("waitlist:"))
async def join_waitlist(callback: CallbackQuery):
    """Добавление в лист ожидания (ТЗ 3.2.2)"""
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    await callback.answer("⏳ Добавляем в список...")

    try:
        # ИЗМЕНЕНИЕ v2: user_id теперь в JSON-теле, не query-параметр
        # Новый формат ответа: {"message": "...", "added": true/false}
        result = await api.join_waitlist(book_id, user_id)

        if result.get("added") is False:
            # Уже в очереди — не ошибка
            await _go_home(
                callback.message,
                user_id,
                "ℹ️ <b>Вы уже в листе ожидания</b>\n\n"
                "Когда книга освободится, вы получите уведомление."
            )
        else:
            await _go_home(
                callback.message,
                user_id,
                "🔔 <b>Вы добавлены в лист ожидания!</b>\n\n"
                "Когда книга освободится, вы получите уведомление.\n\n"
                "💡 Успейте первым забронировать её!"
            )

    except Exception as e:
        error_msg = str(e)
        if "400" in error_msg:
            if "свою" in error_msg.lower() or "owner" in error_msg.lower():
                text = "❌ Нельзя встать в очередь на свою книгу."
            elif "держите" in error_msg.lower() or "borrower" in error_msg.lower():
                text = "❌ Нельзя встать в очередь на книгу, которую держите."
            else:
                text = "❌ Ошибка добавления в лист ожидания."
            await _go_home(callback.message, user_id, text)
        else:
            await callback.answer("Ошибка добавления в лист ожидания", show_alert=True)
        print(f"Error joining waitlist: {e}")


@router.callback_query(F.data.startswith("leave_waitlist:"))
async def leave_waitlist(callback: CallbackQuery):
    """НОВЫЙ v2: Покинуть лист ожидания"""
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    try:
        await api.leave_waitlist(book_id, user_id)
        await _go_home(
            callback.message,
            user_id,
            "✅ Вы покинули лист ожидания."
        )
    except Exception as e:
        await callback.answer("Ошибка", show_alert=True)
        print(f"Error leaving waitlist: {e}")


# ---------------------------------------------------------------------------
# ВОЗВРАТ КНИГИ
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("return:"))
async def start_return_book(callback: CallbackQuery, state: FSMContext):
    """Начало процесса возврата книги (ТЗ 3.3)"""
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_list

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

        try:
            await callback.message.delete()
        except Exception:
            pass

        await callback.message.answer(
            f"📖 <b>{book['title']}</b>\n\n"
            f"📸 <b>Возврат книги</b>\n\n"
            f"Загрузите фотографию книги для подтверждения возврата.\n"
            f"Это поможет владельцу проверить состояние.\n\n"
            f"Или нажмите «Пропустить».",
            reply_markup=return_photo_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка возврата", show_alert=True)
        print(f"Error in start_return: {e}")


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
        photo_data = file.read()
        loading = await message.answer("⏳ Обрабатываем возврат...")
        await _do_return(loading, message.from_user.id, state, photo_data)
    except Exception as e:
        await message.answer("❌ Ошибка обработки фото. Попробуйте ещё раз.")
        print(f"Error processing return photo: {e}")


@router.message(ReturnBookStates.upload_photo)
async def invalid_return_photo(message: Message):
    """Неправильный формат фото"""
    await message.answer(
        "❌ Пожалуйста, отправьте <b>изображение</b> или нажмите «Пропустить»",
        reply_markup=return_photo_keyboard(),
        parse_mode="HTML"
    )


async def _do_return(message: Message, user_id: int, state: FSMContext, photo_data: bytes = None):
    """
    Выполняет возврат книги через API.
    КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ v2: is_admin удалён из FormData.
    Сервер определяет права по admin-флагу пользователя в БД.
    Ответ: {"status": "returned"}
    """
    data = await state.get_data()
    book_id = data.get("return_book_id")

    try:
        await api.return_book(book_id, user_id, photo_bytes=photo_data)
        await state.clear()
        await _go_home(
            message,
            user_id,
            "✅ <b>Книга успешно возвращена!</b>\n\n"
            "Книга снова доступна в каталоге.\n"
            "Владелец получил уведомление о возврате."
        )
    except Exception as e:
        await _go_home(
            message,
            user_id,
            "❌ Ошибка возврата книги. Обратитесь к администратору."
        )
        print(f"Error returning book: {e}")
