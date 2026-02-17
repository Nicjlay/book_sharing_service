"""
Handlers для "Мои книги" и истории книг
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from api.client import api
from keyboards.inline import book_card_keyboard, main_menu_keyboard
from utils.formatters import format_my_books, format_history
from utils.telegram import safe_edit_message
from config import settings

router = Router()


@router.message(Command("mybooks"))
@router.callback_query(F.data == "my_books")
async def show_my_books(event: Message | CallbackQuery, state: FSMContext):
    """
    Показать мои книги (владелец или заемщик)
    """
    await state.clear()
    
    user_id = event.from_user.id
    
    try:
        # Получаем книги пользователя
        books = await api.get_books(user_id=user_id)
        
        if not books:
            text = (
                "📚 <b>Мои книги</b>\n\n"
                "У вас пока нет книг.\n\n"
                "💡 Вы можете:\n"
                "• Забронировать книгу из каталога\n"
                "• Попросить админа добавить вашу книгу"
            )
            
            from keyboards.inline import main_menu_keyboard
            keyboard = main_menu_keyboard(user_id in settings.admin_ids_list)
            
            if isinstance(event, CallbackQuery):
                await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                await event.answer()
            else:
                await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
            return
        
        # Сохраняем книги для навигации
        await state.update_data(my_books=books, current_my_book_index=0)
        
        # Формируем список
        text = format_my_books(books, user_id)
        
        # Добавляем кнопки для детального просмотра
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        
        builder = InlineKeyboardBuilder()
        
        # Группируем книги по tg_id (не по внутреннему DB id!)
        owned = [b for b in books if b.get("owner_tg_id") == user_id]
        borrowed = [b for b in books if b.get("borrower_tg_id") == user_id]

        if owned:
            for book in owned[:5]:  # Ограничиваем до 5 для красоты
                status_emoji = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}
                emoji = status_emoji.get(book.get("status"), "⚪️")
                builder.row(
                    InlineKeyboardButton(
                        text=f"{emoji} {book['title'][:30]}...",
                        callback_data=f"book:{book['id']}"
                    )
                )

        if borrowed:
            for book in borrowed[:5]:
                builder.row(
                    InlineKeyboardButton(
                        text=f"📖 {book['title'][:30]}...",
                        callback_data=f"book:{book['id']}"
                    )
                )

        builder.row(
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")
        )

        if isinstance(event, CallbackQuery):
            await event.message.edit_text(
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
            await event.answer()
        else:
            await event.answer(
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )

    except Exception as e:
        text = "❌ Ошибка загрузки книг"
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        print(f"Error loading my books: {e}")


@router.callback_query(F.data.startswith("history:"))
async def show_book_history(callback: CallbackQuery):
    """
    Показать историю книги (ТЗ 4.4)
    """
    book_id = int(callback.data.split(":")[1])

    try:
        # Получаем историю
        history = await api.get_book_history(book_id)

        if not history:
            await callback.answer("История пуста", show_alert=True)
            return

        # Форматируем историю
        text = format_history(history)

        # Кнопка назад к книге
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🔙 К книге", callback_data=f"book:{book_id}")
        )

        await safe_edit_message(
            callback.message,
            text,
            reply_markup=builder.as_markup()
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки истории", show_alert=True)
        print(f"Error loading history: {e}")



@router.callback_query(F.data.startswith("delete:"))
async def confirm_delete_book(callback: CallbackQuery):
    """
    Подтверждение удаления книги
    """
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    try:
        book = await api.get_book(book_id)

        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return

        # Проверка прав — сравниваем Telegram ID, не DB id
        if book.get("owner_tg_id") != user_id:
            await callback.answer("Только владелец может удалить книгу", show_alert=True)
            return

        # Проверка статуса
        if book.get("status") in ["borrowed", "reserved"]:
            await callback.answer(
                "Нельзя удалить книгу, которая забронирована или выдана",
                show_alert=True
            )
            return

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="✅ Да, удалить",
                callback_data=f"confirm_delete:{book_id}"
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"book:{book_id}"
            )
        )

        confirm_text = (
            f"❓ <b>Подтверждение удаления</b>\n\n"
            f"Вы уверены, что хотите удалить книгу?\n\n"
            f"📖 {book['title']}\n"
            f"✍️ {book['author']}\n\n"
            f"⚠️ Это действие нельзя отменить!"
        )

        await safe_edit_message(
            callback.message,
            confirm_text,
            reply_markup=builder.as_markup()
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка", show_alert=True)
        print(f"Error in confirm_delete: {e}")


@router.callback_query(F.data.startswith("confirm_delete:"))
async def delete_book(callback: CallbackQuery):
    """
    Окончательное удаление книги
    """
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    try:
        await api.delete_book(book_id, user_id)

        await safe_edit_message(
            callback.message,
            "✅ <b>Книга удалена</b>\n\n"
            "Книга перемещена в архив и больше не отображается в каталоге."
        )
        await callback.answer("Удалено", show_alert=True)

    except Exception as e:
        await callback.answer("Ошибка удаления", show_alert=True)
        print(f"Error deleting book: {e}")


# Placeholder для редактирования (можно реализовать позже)
@router.callback_query(F.data.startswith("edit:"))
async def edit_book(callback: CallbackQuery):
    """
    Редактирование книги (TODO: полная реализация как в визарде)
    """
    await callback.answer(
        "Функция редактирования в разработке.\n"
        "Пока можно удалить книгу и добавить заново.",
        show_alert=True
    )