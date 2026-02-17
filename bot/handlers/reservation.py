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
    reservation_days_keyboard,
    return_photo_keyboard,
    book_card_keyboard,
    cancel_keyboard
)
from utils.validators import validate_days
from utils.telegram import safe_edit_message
from config import settings

router = Router()


# --- БРОНИРОВАНИЕ ---

@router.callback_query(F.data.startswith("reserve:"))
async def start_reservation(callback: CallbackQuery, state: FSMContext):
    """
    Начало бронирования книги (ТЗ 3.2.2)
    """
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    try:
        # Проверяем, что книга существует и доступна
        book = await api.get_book(book_id)

        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        if book["status"] != "available":
            await callback.answer("Книга уже занята", show_alert=True)
            return

        # Сохраняем book_id в состояние
        await state.update_data(reserve_book_id=book_id)
        await state.set_state(ReservationStates.select_days)

        # Предлагаем выбрать срок
        await safe_edit_message(
            callback.message,
            f"📖 <b>{book['title']}</b>\n"
            f"✍️ {book['author']}\n\n"
            f"📅 <b>Выберите срок бронирования:</b>\n\n"
            f"На какой срок вы хотите взять книгу?",
            reply_markup=reservation_days_keyboard()
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка бронирования", show_alert=True)
        print(f"Error in start_reservation: {e}")


@router.callback_query(F.data.startswith("days:"), ReservationStates.select_days)
async def select_reservation_days(callback: CallbackQuery, state: FSMContext):
    """
    Выбор срока бронирования (ТЗ 3.2.2)
    """
    days_str = callback.data.split(":")[1]

    if days_str == "custom":
        # Пользователь хочет ввести свой срок
        await safe_edit_message(
            callback.message,
            "✏️ Введите количество дней (1-90):",
            reply_markup=cancel_keyboard()
        )
        await state.set_state(ReservationStates.custom_days)
        await callback.answer()
        return

    days = int(days_str)

    # Отправляем запрос на бронирование
    await process_reservation_request(callback, state, days)


@router.message(ReservationStates.custom_days)
async def process_custom_days(message: Message, state: FSMContext):
    """
    Обработка ввода произвольного срока
    """
    is_valid, days, error_msg = validate_days(message.text)

    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return

    # Создаем "фейковый" callback для использования общей функции
    # На самом деле отправляем запрос
    data = await state.get_data()
    book_id = data.get("reserve_book_id")
    user_id = message.from_user.id

    try:
        result = await api.request_reservation(book_id, user_id, days)

        await state.clear()

        # Уведомление пользователю
        due_date = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")

        await message.answer(
            f"✅ <b>Запрос на бронирование отправлен!</b>\n\n"
            f"📅 Желаемый срок: {days} дней (до {due_date})\n\n"
            f"⏳ Ожидайте подтверждения от администратора.\n"
            f"Вы получите уведомление, когда заявка будет обработана.",
            parse_mode="HTML"
        )

    except Exception as e:
        if "409" in str(e):
            # Книга занята - предлагаем waitlist
            await message.answer(
                f"❌ Книга уже забронирована.\n\n"
                f"🔔 Хотите получить уведомление, когда она освободится?",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Ошибка бронирования")
        print(f"Error in reservation: {e}")


async def process_reservation_request(callback: CallbackQuery, state: FSMContext, days: int):
    """
    Отправка запроса на бронирование в API
    """
    data = await state.get_data()
    book_id = data.get("reserve_book_id")
    user_id = callback.from_user.id

    try:
        result = await api.request_reservation(book_id, user_id, days)

        await state.clear()

        # Уведомление пользователю
        due_date = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")

        await safe_edit_message(
            callback.message,
            f"✅ <b>Запрос на бронирование отправлен!</b>\n\n"
            f"📅 Желаемый срок: {days} дней (до {due_date})\n\n"
            f"⏳ Ожидайте подтверждения от администратора.\n"
            f"Вы получите уведомление, когда заявка будет обработана."
        )
        await callback.answer("Запрос отправлен!", show_alert=True)

    except Exception as e:
        error_str = str(e)

        if "409" in error_str or "занята" in error_str.lower():
            await safe_edit_message(
                callback.message,
                f"❌ <b>Книга уже забронирована</b>\n\n"
                f"🔔 Хотите получить уведомление, когда она освободится?"
            )
            await callback.answer()
        else:
            await callback.answer("Ошибка бронирования", show_alert=True)
            print(f"Error in reservation: {e}")


# --- WAITLIST ---

@router.callback_query(F.data.startswith("waitlist:"))
async def join_waitlist(callback: CallbackQuery):
    """
    Добавление в лист ожидания (ТЗ 3.2.2)
    """
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    try:
        await api.join_waitlist(book_id, user_id)

        await safe_edit_message(
            callback.message,
            f"🔔 <b>Вы добавлены в лист ожидания!</b>\n\n"
            f"Когда книга освободится, вы получите уведомление.\n\n"
            f"💡 Успейте первым забронировать её!"
        )
        await callback.answer("Добавлено в лист ожидания", show_alert=True)

    except Exception as e:
        await callback.answer("Ошибка добавления в лист ожидания", show_alert=True)
        print(f"Error joining waitlist: {e}")


# --- ВОЗВРАТ КНИГИ ---

@router.callback_query(F.data.startswith("return:"))
async def start_return_book(callback: CallbackQuery, state: FSMContext):
    """
    Начало процесса возврата книги (ТЗ 3.3)
    """
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_list

    try:
        # Проверяем права на возврат
        book = await api.get_book(book_id)

        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        # Проверяем права на возврат — сравниваем Telegram ID, не DB id
        is_borrower = book.get("borrower_tg_id") == user_id
        is_owner = book.get("owner_tg_id") == user_id

        if not (is_borrower or is_owner or is_admin):
            await callback.answer("У вас нет прав на возврат этой книги", show_alert=True)
            return

        # Сохраняем book_id
        await state.update_data(return_book_id=book_id)
        await state.set_state(ReturnBookStates.upload_photo)

        # Запрашиваем фото (ТЗ 3.3.2)
        await safe_edit_message(
            callback.message,
            f"📖 <b>{book['title']}</b>\n\n"
            f"📸 <b>Возврат книги</b>\n\n"
            f"Загрузите фотографию книги для подтверждения возврата.\n\n"
            f"💡 Это поможет владельцу проверить состояние книги.\n\n"
            f"Можно пропустить этот шаг.",
            reply_markup=return_photo_keyboard()
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка возврата", show_alert=True)
        print(f"Error in start_return: {e}")


@router.callback_query(F.data == "skip_return_photo", ReturnBookStates.upload_photo)
async def skip_return_photo(callback: CallbackQuery, state: FSMContext):
    """
    Пропуск фото при возврате (ТЗ 3.3.2)
    """
    await process_book_return(callback.message, callback.from_user.id, state, photo_data=None)
    await callback.answer("Фото пропущено")


@router.message(ReturnBookStates.upload_photo, F.photo)
async def process_return_photo(message: Message, state: FSMContext):
    """
    Обработка фото при возврате
    """
    photo = message.photo[-1]

    try:
        # Скачиваем фото
        file = await message.bot.download(photo.file_id)
        photo_data = file.read()

        await message.answer("✅ Фото получено, обрабатываем возврат...")
        await process_book_return(message, message.from_user.id, state, photo_data)

    except Exception as e:
        await message.answer("❌ Ошибка обработки фото")
        print(f"Error processing return photo: {e}")


@router.message(ReturnBookStates.upload_photo)
async def invalid_return_photo(message: Message):
    """
    Неправильный формат фото
    """
    await message.answer(
        "❌ Пожалуйста, отправьте <b>изображение</b> или нажмите «Пропустить фото»",
        reply_markup=return_photo_keyboard(),
        parse_mode="HTML"
    )


async def process_book_return(message: Message, user_id: int, state: FSMContext, photo_data: bytes = None):
    """
    Обработка возврата книги через API (ТЗ 3.3.3)
    """
    data = await state.get_data()
    book_id = data.get("return_book_id")
    is_admin = user_id in settings.admin_ids_list

    try:
        result = await api.return_book(book_id, user_id, is_admin, photo_data)

        await state.clear()

        await message.answer(
            f"✅ <b>Книга успешно возвращена!</b>\n\n"
            f"Книга снова доступна в каталоге.\n"
            f"Владелец получил уведомление о возврате.",
            parse_mode="HTML"
        )

        # API автоматически:
        # 1. Отправит уведомление владельцу
        # 2. Уведомит пользователей из waitlist

    except Exception as e:
        await message.answer("❌ Ошибка возврата книги")
        print(f"Error returning book: {e}")