"""
Handlers для админ панели (ТЗ 3.2.3)
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta

from api.client import api
from keyboards.inline import admin_panel_keyboard, book_card_keyboard, cancel_keyboard
from utils.formatters import format_book_card
from config import settings
from states.wizard import AdminRejectStates, AdminApproveStates
import os

router = Router()

MEDIA_ROOT = os.getenv("MEDIA_UPLOAD_DIR", "/app/media")


async def get_photo_input(image_path: str) -> BufferedInputFile | None:
    """Читает обложку с диска (shared volume) или скачивает через API как fallback."""
    full_path = os.path.join(MEDIA_ROOT, image_path)

    if os.path.exists(full_path):
        with open(full_path, "rb") as f:
            return BufferedInputFile(f.read(), filename=os.path.basename(full_path))

    photo_bytes = await api.get_image_bytes(image_path)
    if photo_bytes:
        return BufferedInputFile(photo_bytes, filename=os.path.basename(image_path))

    return None


def admin_only(func):
    """Декоратор для проверки прав админа"""
    async def wrapper(event: Message | CallbackQuery, *args, **kwargs):
        user_id = event.from_user.id
        if user_id not in settings.admin_ids_list:
            text = "❌ Эта функция доступна только администраторам."
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)
            return
        return await func(event, *args, **kwargs)
    return wrapper


@router.message(Command("admin"))
@router.callback_query(F.data == "admin_panel")
@admin_only
async def show_admin_panel(event: Message | CallbackQuery, **kwargs):
    """
    Админ панель - главное меню
    """
    text = (
        "👨‍💼 <b>Админ панель</b>\n\n"
        "Управление библиотекой:"
    )

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(
            text,
            reply_markup=admin_panel_keyboard(),
            parse_mode="HTML"
        )
        await event.answer()
    else:
        await event.answer(
            text,
            reply_markup=admin_panel_keyboard(),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "admin_reservations")
@admin_only
async def show_pending_reservations(callback: CallbackQuery, state: FSMContext, **kwargs):
    """
    Показать заявки на бронирование (ТЗ 3.2.3)
    """
    try:
        books = await api.get_pending_reservations()

        if not books:
            await callback.message.edit_text(
                "📋 <b>Заявки на бронирование</b>\n\n"
                "📭 Нет ожидающих заявок",
                reply_markup=admin_panel_keyboard(),
                parse_mode="HTML"
            )
            await callback.answer()
            return

        # Сохраняем книги в состояние для навигации
        await state.update_data(pending_books=books, current_pending_index=0)

        # Показываем первую заявку
        await show_reservation_request(callback.message, books[0], 0, len(books))
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки заявок", show_alert=True)
        print(f"Error loading pending reservations: {e}")


async def show_reservation_request(message: Message, book: dict, index: int, total: int):
    """
    Показать заявку на бронирование с кнопками действий
    """
    text = f"📋 <b>Заявка {index + 1} из {total}</b>\n\n"
    text += format_book_card(book)

    # Добавляем информацию о заемщике
    borrower_name = book.get("borrower_username") or book.get("borrower_full_name", "Неизвестен")
    text += f"\n\n📱 <b>Запрос от:</b> {borrower_name}"

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    builder = InlineKeyboardBuilder()

    # Кнопки действий
    builder.row(
        InlineKeyboardButton(
            text="✅ Выдал книгу",
            callback_data=f"approve:{book['id']}"
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"reject:{book['id']}"
        )
    )

    # Навигация между заявками
    if total > 1:
        nav_buttons = []
        if index > 0:
            nav_buttons.append(
                InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"pending:{index - 1}")
            )
        nav_buttons.append(
            InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data="noop")
        )
        if index < total - 1:
            nav_buttons.append(
                InlineKeyboardButton(text="➡️ След.", callback_data=f"pending:{index + 1}")
            )
        builder.row(*nav_buttons)

    builder.row(
        InlineKeyboardButton(text="🔙 Админ панель", callback_data="admin_panel")
    )

    # Если есть фото
    if book.get("image_path"):
        try:
            photo = await get_photo_input(book["image_path"])
            if not photo:
                raise ValueError("No photo available")

            await message.delete()
            await message.answer_photo(
                photo=photo,
                caption=text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except:
            await message.edit_text(
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
    else:
        await message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("pending:"))
@admin_only
async def navigate_pending(callback: CallbackQuery, state: FSMContext):
    """
    Навигация между заявками
    """
    index = int(callback.data.split(":")[1])

    data = await state.get_data()
    books = data.get("pending_books", [])

    if not books or index >= len(books):
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await state.update_data(current_pending_index=index)
    await show_reservation_request(callback.message, books[index], index, len(books))
    await callback.answer()


@router.callback_query(F.data.startswith("approve:"))
@admin_only
async def approve_reservation(callback: CallbackQuery, state: FSMContext):
    """
    Подтверждение выдачи книги (ТЗ 3.2.3)
    """
    book_id = int(callback.data.split(":")[1])

    # Сохраняем book_id и запрашиваем дату возврата
    await state.update_data(approve_book_id=book_id)
    await state.set_state(AdminApproveStates.due_date)

    # Предлагаем варианты дат
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    builder = InlineKeyboardBuilder()

    # Варианты сроков
    dates = [
        (7, "+1 неделя"),
        (14, "+2 недели"),
        (21, "+3 недели"),
        (30, "+1 месяц")
    ]

    for days, label in dates:
        due_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        display_date = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")
        builder.row(
            InlineKeyboardButton(
                text=f"📅 {label} ({display_date})",
                callback_data=f"set_due_date:{due_date}"
            )
        )

    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_reservations")
    )

    await callback.message.edit_text(
        "✅ <b>Подтверждение выдачи</b>\n\n"
        "Выберите дату возврата:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_due_date:"), AdminApproveStates.due_date)
@admin_only
async def set_due_date_and_approve(callback: CallbackQuery, state: FSMContext):
    """
    Установка даты возврата и подтверждение
    """
    due_date_str = callback.data.split(":", 1)[1]

    data = await state.get_data()
    book_id = data.get("approve_book_id")
    admin_id = callback.from_user.id

    try:
        # Форматируем дату для API
        due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
        due_date_iso = due_date.isoformat()

        # Подтверждаем выдачу
        book = await api.approve_reservation(book_id, admin_id, due_date_iso)

        await state.clear()

        await callback.message.edit_text(
            f"✅ <b>Выдача подтверждена!</b>\n\n"
            f"📖 Книга: {book['title']}\n"
            f"📅 Вернуть до: {due_date.strftime('%d.%m.%Y')}\n\n"
            f"Пользователь получил уведомление.",
            parse_mode="HTML"
        )
        await callback.answer("Выдача подтверждена!", show_alert=True)

        # API автоматически отправит уведомление пользователю

    except Exception as e:
        await callback.answer("Ошибка подтверждения", show_alert=True)
        print(f"Error approving reservation: {e}")


@router.callback_query(F.data.startswith("reject:"))
@admin_only
async def reject_reservation(callback: CallbackQuery, state: FSMContext):
    """
    Отклонение заявки на бронирование
    """
    book_id = int(callback.data.split(":")[1])

    # Сохраняем book_id и запрашиваем причину
    await state.update_data(reject_book_id=book_id)
    await state.set_state(AdminRejectStates.reason)

    await callback.message.edit_text(
        "❌ <b>Отклонение заявки</b>\n\n"
        "Введите причину отклонения (будет отправлена пользователю):\n\n"
        "Например: <i>Книга повреждена</i> или <i>Книга временно недоступна</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminRejectStates.reason)
@admin_only
async def process_reject_reason(message: Message, state: FSMContext):
    """
    Обработка причины отклонения
    """
    reason = message.text.strip()

    if len(reason) < 3:
        await message.answer(
            "❌ Причина слишком короткая. Введите минимум 3 символа.",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    book_id = data.get("reject_book_id")
    admin_id = message.from_user.id

    try:
        await api.reject_reservation(book_id, admin_id, reason)

        await state.clear()

        await message.answer(
            f"❌ <b>Заявка отклонена</b>\n\n"
            f"Причина: {reason}\n\n"
            f"Пользователь получил уведомление.",
            parse_mode="HTML"
        )

        # API автоматически отправит уведомление пользователю

    except Exception as e:
        await message.answer("❌ Ошибка отклонения заявки")
        print(f"Error rejecting reservation: {e}")