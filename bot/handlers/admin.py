"""
Handlers для админ панели (ТЗ 3.2.3)
"""
import functools
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api.client import APIError, api
from config import settings
from keyboards.inline import admin_panel_keyboard, cancel_keyboard, main_menu_keyboard
from states.wizard import AdminApproveStates, AdminRejectStates
from utils.formatters import escape_html, format_book_card
from utils.telegram import get_photo_input, safe_delete_message, safe_edit_message

logger = logging.getLogger(__name__)
router = Router()


def admin_only(func):
    """
    Декоратор проверки прав администратора.

    @functools.wraps обязателен — aiogram 3.x использует inspect.signature(),
    который через __wrapped__ видит оригинальные параметры (state, bot и т.д.)
    и правильно инжектирует зависимости. Без @functools.wraps DI сломается.
    """
    @functools.wraps(func)
    async def wrapper(event: Message | CallbackQuery, **kwargs):
        user_id = event.from_user.id
        if user_id not in settings.admin_ids_set:
            text = "❌ Эта функция доступна только администраторам."
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)
            return
        return await func(event, **kwargs)
    return wrapper


async def _go_admin(message: Message, text: str):
    """Удаляет текущее сообщение и показывает панель администратора."""
    await safe_delete_message(message)
    await message.answer(text, reply_markup=admin_panel_keyboard(), parse_mode="HTML")


# ---------------------------------------------------------------------------
# ПАНЕЛЬ
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
@router.callback_query(F.data == "admin_panel")
@admin_only
async def show_admin_panel(event: Message | CallbackQuery, **kwargs):
    """Главное меню админ панели"""
    text = "👨‍💼 <b>Админ панель</b>\n\nУправление библиотекой:"
    if isinstance(event, CallbackQuery):
        await safe_edit_message(event.message, text, reply_markup=admin_panel_keyboard())
        await event.answer()
    else:
        await event.answer(text, reply_markup=admin_panel_keyboard(), parse_mode="HTML")


# ---------------------------------------------------------------------------
# ЗАЯВКИ НА БРОНИРОВАНИЕ
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "admin_reservations")
@admin_only
async def show_pending_reservations(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Показать заявки на бронирование (ТЗ 3.2.3)"""
    try:
        requester_id = callback.from_user.id
        books = await api.get_pending_reservations(requester_id=requester_id)

        if not books:
            await safe_edit_message(
                callback.message,
                "📋 <b>Заявки на бронирование</b>\n\n📭 Нет ожидающих заявок",
                reply_markup=admin_panel_keyboard(),
            )
            await callback.answer()
            return

        # FIX: b.get("id") вместо b["id"] — KeyError если поле отсутствует.
        # Книги без id фильтруем: они не могут быть обработаны через API,
        # и их присутствие в кеше приведёт к ошибке при approve/reject.
        book_ids = [b.get("id") for b in books if b.get("id")]
        await state.update_data(
            pending_book_ids=book_ids,
            pending_books_cache=books,
            current_pending_index=0,
        )
        await show_reservation_request(callback.message, books[0], 0, len(books))
        await callback.answer()

    except APIError as e:
        if e.status == 403:
            await callback.answer("❌ Нет прав администратора", show_alert=True)
        else:
            await callback.answer("Ошибка загрузки заявок", show_alert=True)
        logger.error("Error loading pending reservations: HTTP %d %s", e.status, e.message)
    except Exception as e:
        await callback.answer("Ошибка загрузки заявок", show_alert=True)
        logger.error("Error loading pending reservations: %s", e)


async def show_reservation_request(message: Message, book: dict, index: int, total: int):
    """
    Показать заявку на бронирование с кнопками действий.

    При навигации между заявками с фото: если текущее сообщение уже является
    фото — редактируем caption вместо создания нового (избегаем накопления
    фото-сообщений при быстрой навигации).
    """
    borrower_id = book.get("borrower_id")
    borrower_username = book.get("borrower_username")
    borrower_full_name = book.get("borrower_full_name", "Неизвестен")

    safe_full_name = escape_html(borrower_full_name)
    safe_username = escape_html(borrower_username) if borrower_username else None

    if safe_username:
        borrower_link = f'<a href="tg://user?id={borrower_id}">@{safe_username}</a>'
    elif borrower_id:
        borrower_link = f'<a href="tg://user?id={borrower_id}">{safe_full_name}</a>'
    else:
        borrower_link = safe_full_name

    text = f"📋 <b>Заявка {index + 1} из {total}</b>\n\n"
    text += format_book_card(book)
    text += f"\n\n📱 <b>Запрос от:</b> {borrower_link}"

    # FIX: book.get("id", 0) вместо book["id"] — KeyError если id отсутствует.
    # ID 0 технически невалиден, но в этом случае кнопки approve/reject всё равно
    # будут показаны (на них нажать можно, но API вернёт 404 или 400 — пользователь
    # увидит внятную ошибку). Это лучше, чем KeyError который крашит хендлер.
    book_id = book.get("id", 0)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Выдал книгу", callback_data=f"approve:{book_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{book_id}"),
    )

    if total > 1:
        nav_buttons = []
        if index > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"pending:{index - 1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data="noop"))
        if index < total - 1:
            nav_buttons.append(InlineKeyboardButton(text="➡️ След.", callback_data=f"pending:{index + 1}"))
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="🔔 Обновить список", callback_data="admin_reservations"))
    builder.row(InlineKeyboardButton(text="🔙 Админ панель", callback_data="admin_panel"))

    markup = builder.as_markup()

    if book.get("image_path"):
        # Если текущее сообщение уже является фото-сообщением —
        # редактируем caption вместо создания нового (избегаем спам-дублирования).
        if message.photo:
            try:
                await safe_edit_message(message, text, reply_markup=markup)
                return
            except Exception as e:
                logger.warning(
                    "Could not edit caption for book %s, will resend: %s",
                    book_id, e,
                )

        # Текущее сообщение не фото или редактирование не удалось — загружаем фото и шлём новое.
        try:
            photo = await get_photo_input(book["image_path"])
            if photo:
                await safe_delete_message(message)
                await message.answer_photo(
                    photo=photo,
                    caption=text,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
                return
        except Exception as e:
            logger.warning("Photo display error for book %s: %s", book_id, e)

    # Нет фото или загрузка провалилась — текстовая карточка
    await safe_edit_message(message, text, reply_markup=markup)


@router.callback_query(F.data.startswith("pending:"))
@admin_only
async def navigate_pending(callback: CallbackQuery, state: FSMContext, **kwargs):
    """
    Навигация между заявками.

    Кеш заявок может устареть если другой администратор обработал заявку пока
    текущий листает список. Проверяем актуальность через API.
    """
    try:
        index = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные", show_alert=True)
        return

    data = await state.get_data()
    books = data.get("pending_books_cache", [])

    if not books or index >= len(books) or index < 0:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    book = books[index]
    # FIX: book.get("id") вместо book["id"] — защита от KeyError при неполном ответе API.
    book_id = book.get("id")

    if not book_id:
        await callback.answer("Некорректные данные заявки", show_alert=True)
        return

    # Проверяем актуальность статуса книги
    try:
        fresh_book = await api.get_book(book_id)
        if fresh_book and fresh_book.get("status") != "reserved":
            # Заявка уже обработана — перезагружаем список
            requester_id = callback.from_user.id
            try:
                fresh_books = await api.get_pending_reservations(requester_id=requester_id)
            except Exception as reload_err:
                logger.error("Failed to reload pending reservations: %s", reload_err)
                await callback.answer(
                    "⚠️ Заявка уже обработана, но обновить список не удалось. "
                    "Нажмите «Обновить список».",
                    show_alert=True,
                )
                return

            await callback.answer(
                "⚠️ Эта заявка уже обработана. Список обновлён.",
                show_alert=True,
            )

            if not fresh_books:
                await safe_edit_message(
                    callback.message,
                    "📋 <b>Заявки на бронирование</b>\n\n📭 Нет ожидающих заявок",
                    reply_markup=admin_panel_keyboard(),
                )
                await state.update_data(pending_books_cache=[], pending_book_ids=[])
                return

            new_index = min(index, len(fresh_books) - 1)
            # FIX: b.get("id") вместо b["id"] — KeyError при неполном ответе
            new_book_ids = [b.get("id") for b in fresh_books if b.get("id")]
            await state.update_data(
                pending_books_cache=fresh_books,
                pending_book_ids=new_book_ids,
                current_pending_index=new_index,
            )
            await show_reservation_request(callback.message, fresh_books[new_index], new_index, len(fresh_books))
            return

        if fresh_book:
            books[index] = fresh_book
            await state.update_data(pending_books_cache=books)
    except Exception as e:
        # Не смогли проверить свежесть — продолжаем с кешем (не критично)
        logger.warning("Could not verify book %s freshness: %s", book_id, e)

    await state.update_data(current_pending_index=index)
    await show_reservation_request(callback.message, books[index], index, len(books))
    await callback.answer()


# ---------------------------------------------------------------------------
# ПОДТВЕРЖДЕНИЕ ВЫДАЧИ
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("approve:"))
@admin_only
async def approve_reservation(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Подтверждение выдачи книги (ТЗ 3.2.3)"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные", show_alert=True)
        return

    await state.update_data(approve_book_id=book_id)
    await state.set_state(AdminApproveStates.due_date)

    builder = InlineKeyboardBuilder()
    now_utc = datetime.now(timezone.utc)
    dates = [(7, "+1 неделя"), (14, "+2 недели"), (21, "+3 недели"), (30, "+1 месяц")]
    for days, label in dates:
        due_date_str = (now_utc + timedelta(days=days)).strftime("%Y-%m-%d")
        display_date = (now_utc + timedelta(days=days)).strftime("%d.%m.%Y")
        builder.row(InlineKeyboardButton(
            text=f"📅 {label} ({display_date})",
            callback_data=f"set_due_date:{due_date_str}",
        ))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_reservations"))

    await safe_delete_message(callback.message)
    await callback.message.answer(
        "✅ <b>Подтверждение выдачи</b>\n\nВыберите дату возврата:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_due_date:"), AdminApproveStates.due_date)
@admin_only
async def set_due_date_and_approve(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Установка даты возврата и подтверждение"""
    # FIX: убрали лишний round-trip datetime.strptime(due_date_str, "%Y-%m-%d").strftime("%Y-%m-%d").
    # Строка due_date_str уже в формате YYYY-MM-DD (сформирована выше через strftime).
    # Валидируем формат через strptime только для отображения пользователю,
    # а в API передаём оригинальную строку — без повторного форматирования.
    try:
        due_date_str = callback.data.split(":", 1)[1]
        # Валидация формата даты — гарантирует что строка корректна перед отправкой в API
        due_date_display = datetime.strptime(due_date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (IndexError, ValueError):
        await callback.answer("Некорректная дата", show_alert=True)
        return

    data = await state.get_data()
    book_id = data.get("approve_book_id")
    if not book_id:
        await callback.answer("Сессия устарела, начните снова", show_alert=True)
        await state.clear()
        return

    admin_id = callback.from_user.id

    await safe_edit_message(callback.message, "⏳ Подтверждаем выдачу...")
    await callback.answer()

    try:
        # Передаём оригинальную строку due_date_str — API ожидает YYYY-MM-DD
        book = await api.approve_reservation(book_id, admin_id, due_date_str)
        await state.clear()
        await _go_admin(
            callback.message,
            f"✅ <b>Выдача подтверждена!</b>\n\n"
            f"📖 {escape_html(book.get('title', '—'))}\n"
            f"📅 Вернуть до: {due_date_display}\n\n"
            f"Пользователь получил уведомление.",
        )
    except APIError as e:
        await state.clear()
        if e.status == 422:
            text = "❌ Некорректная дата возврата. Дата должна быть в будущем и не позже 730 дней."
        elif e.status == 403:
            text = "❌ Нет прав администратора."
        elif e.status == 400:
            text = "❌ Книга уже не ожидает подтверждения (возможно, другой администратор уже обработал заявку)."
        else:
            text = "❌ Ошибка подтверждения выдачи. Попробуйте ещё раз."
        await _go_admin(callback.message, text)
        logger.error("Error approving reservation for book %s: HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await state.clear()
        await _go_admin(callback.message, "❌ Ошибка подтверждения выдачи. Попробуйте ещё раз.")
        logger.error("Error approving reservation for book %s: %s", book_id, e)


# ---------------------------------------------------------------------------
# ОТКЛОНЕНИЕ ЗАЯВКИ
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("reject:"))
@admin_only
async def reject_reservation(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Отклонение заявки на бронирование"""
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные", show_alert=True)
        return

    await state.update_data(reject_book_id=book_id)
    await state.set_state(AdminRejectStates.reason)

    await safe_delete_message(callback.message)
    await callback.message.answer(
        "❌ <b>Отклонение заявки</b>\n\n"
        "Введите причину (будет отправлена пользователю):\n\n"
        "Например: <i>Книга повреждена</i> или <i>Книга временно недоступна</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminRejectStates.reason)
@admin_only
async def process_reject_reason(message: Message, state: FSMContext, **kwargs):
    """Обработка причины отклонения"""
    reason = message.text.strip() if message.text else ""

    if len(reason) < 3:
        await message.answer("❌ Причина слишком короткая. Введите минимум 3 символа.")
        return

    if len(reason) > 500:
        await message.answer("❌ Причина слишком длинная. Максимум 500 символов.")
        return

    data = await state.get_data()
    book_id = data.get("reject_book_id")
    if not book_id:
        await state.clear()
        await message.answer("❌ Сессия устарела. Начните процесс отклонения заново.")
        return

    admin_id = message.from_user.id
    loading = await message.answer("⏳ Отклоняем заявку...")

    try:
        await api.reject_reservation(book_id, admin_id, reason)
        await state.clear()
        await safe_delete_message(loading)
        await message.answer(
            f"❌ <b>Заявка отклонена</b>\n\n"
            f"Причина: {escape_html(reason)}\n\n"
            f"Пользователь получил уведомление.",
            reply_markup=admin_panel_keyboard(),
            parse_mode="HTML",
        )

    except APIError as e:
        await safe_delete_message(loading)
        await state.clear()
        if e.status == 400:
            text = "❌ Книга не в статусе ожидания. Возможно, заявка уже обработана."
        elif e.status == 403:
            text = "❌ Нет прав администратора."
        else:
            text = "❌ Ошибка отклонения заявки. Попробуйте ещё раз."
        await message.answer(text, reply_markup=admin_panel_keyboard(), parse_mode="HTML")
        logger.error("Error rejecting reservation for book %s: HTTP %d %s", book_id, e.status, e.message)
    except Exception as e:
        await safe_delete_message(loading)
        await state.clear()
        await message.answer(
            "❌ Ошибка отклонения заявки. Попробуйте ещё раз.",
            reply_markup=admin_panel_keyboard(),
            parse_mode="HTML",
        )
        logger.error("Error rejecting reservation for book %s: %s", book_id, e)