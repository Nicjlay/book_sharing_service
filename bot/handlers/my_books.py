"""
Handlers для "Мои книги", "Книги на руках" и истории книг
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from datetime import datetime

from api.client import api
from keyboards.inline import (
    book_card_keyboard, main_menu_keyboard,
    borrowed_books_keyboard, borrowed_book_actions_keyboard
)
from utils.formatters import format_my_books, format_history, format_book_card
from utils.telegram import safe_edit_message
from config import settings

router = Router()


@router.message(Command("mybooks"))
@router.callback_query(F.data == "my_books")
async def show_my_books(event: Message | CallbackQuery, state: FSMContext):
    """Показать мои книги (владелец или заемщик)"""
    await state.clear()
    user_id = event.from_user.id

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
            keyboard = main_menu_keyboard(user_id in settings.admin_ids_list)
            if isinstance(event, CallbackQuery):
                await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                await event.answer()
            else:
                await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
            return

        text = format_my_books(books, user_id)

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton

        builder = InlineKeyboardBuilder()
        owned = [b for b in books if b.get("owner_id") == user_id]
        borrowed = [b for b in books if b.get("borrower_id") == user_id]

        if owned:
            for book in owned[:5]:
                status_emoji = {"available": "🟢", "reserved": "🟡", "borrowed": "🔴", "overdue": "⏳"}
                emoji = status_emoji.get(book.get("status"), "⚪️")
                title = book["title"][:28] + ".." if len(book["title"]) > 28 else book["title"]
                builder.row(InlineKeyboardButton(
                    text=f"{emoji} {title}",
                    callback_data=f"book:{book['id']}"
                ))

        if borrowed:
            builder.row(InlineKeyboardButton(
                text="📖 Книги на руках (" + str(len(borrowed)) + ")",
                callback_data="my_borrowed"
            ))

        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu"))

        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
            await event.answer()
        else:
            await event.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

    except Exception as e:
        text = "❌ Ошибка загрузки книг"
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        print(f"Error loading my books: {e}")


@router.callback_query(F.data == "my_borrowed")
async def show_borrowed_books(callback: CallbackQuery):
    """Список книг, которые пользователь взял"""
    user_id = callback.from_user.id

    try:
        books = await api.get_books(user_id=user_id)
        borrowed = [
            b for b in books
            if b.get("borrower_id") == user_id
            and b.get("status") in ("borrowed", "overdue", "reserved")
        ]

        if not borrowed:
            await safe_edit_message(
                callback.message,
                "📖 <b>Книги на руках</b>\n\n"
                "У вас нет книг на руках.\n\n"
                "Перейдите в каталог, чтобы забронировать книгу.",
                reply_markup=main_menu_keyboard(user_id in settings.admin_ids_list)
            )
            await callback.answer()
            return

        status_label = {"borrowed": "🔴 Выдана", "overdue": "⏳ Просрочена", "reserved": "🟡 Ожидает выдачи"}
        text = "📖 <b>Книги на руках:</b>\n\n"
        for book in borrowed:
            label = status_label.get(book.get("status"), "📖")
            due = ""
            if book.get("return_due_date"):
                try:
                    d = datetime.fromisoformat(book["return_due_date"].replace("Z", "+00:00"))
                    due = f" · до {d.strftime('%d.%m.%Y')}"
                except Exception:
                    pass
            text += f"{label}{due}\n<b>{book['title']}</b>\n✍️ {book['author']}\n\n"

        text += "👇 Выберите книгу для возврата:"

        await safe_edit_message(callback.message, text, reply_markup=borrowed_books_keyboard(borrowed))
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки", show_alert=True)
        print(f"Error loading borrowed books: {e}")


@router.callback_query(F.data.startswith("borrowed_detail:"))
async def show_borrowed_book_detail(callback: CallbackQuery):
    """Карточка конкретной книги на руках"""
    book_id = int(callback.data.split(":")[1])

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
                    try:
                        await callback.message.delete()
                    except Exception:
                        pass
                    await callback.message.answer_photo(
                        photo=photo,
                        caption=text,
                        reply_markup=borrowed_book_actions_keyboard(book_id),
                        parse_mode="HTML"
                    )
                    await callback.answer()
                    return
            except Exception as e:
                print(f"Photo error: {e}")

        await safe_edit_message(
            callback.message, text,
            reply_markup=borrowed_book_actions_keyboard(book_id)
        )
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки книги", show_alert=True)
        print(f"Error in borrowed_detail: {e}")


@router.callback_query(F.data.startswith("history:"))
async def show_book_history(callback: CallbackQuery):
    """Показать историю книги (ТЗ 4.4)"""
    book_id = int(callback.data.split(":")[1])

    try:
        history = await api.get_book_history(book_id)

        if not history:
            await callback.answer("История пуста", show_alert=True)
            return

        text = format_history(history)

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 К книге", callback_data=f"book:{book_id}"))

        try:
            await safe_edit_message(callback.message, text, reply_markup=builder.as_markup())
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка загрузки истории", show_alert=True)
        print(f"Error loading history: {e}")


@router.callback_query(F.data.startswith("delete:"))
async def confirm_delete_book(callback: CallbackQuery):
    """Подтверждение удаления книги"""
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    try:
        book = await api.get_book(book_id)
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return
        if book.get("owner_id") != user_id:
            await callback.answer("Только владелец может удалить книгу", show_alert=True)
            return
        if book.get("status") in ("borrowed", "reserved"):
            await callback.answer("Нельзя удалить книгу, которая забронирована или выдана", show_alert=True)
            return

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{book_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"book:{book_id}")
        )
        await safe_edit_message(
            callback.message,
            f"❓ <b>Подтверждение удаления</b>\n\n"
            f"Вы уверены, что хотите удалить книгу?\n\n"
            f"📖 {book['title']}\n✍️ {book['author']}\n\n"
            f"⚠️ Это действие нельзя отменить!",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    except Exception as e:
        await callback.answer("Ошибка", show_alert=True)
        print(f"Error in confirm_delete: {e}")


@router.callback_query(F.data.startswith("confirm_delete:"))
async def delete_book(callback: CallbackQuery):
    """Окончательное удаление книги"""
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    try:
        await api.delete_book(book_id, user_id)
        is_admin = user_id in settings.admin_ids_list
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            "✅ <b>Книга удалена</b>\n\nКнига перемещена в архив.",
            reply_markup=main_menu_keyboard(is_admin),
            parse_mode="HTML"
        )
        await callback.answer("Удалено")
    except Exception as e:
        await callback.answer("Ошибка удаления", show_alert=True)
        print(f"Error deleting book: {e}")


@router.callback_query(F.data.startswith("edit:"))
async def edit_book(callback: CallbackQuery):
    await callback.answer(
        "Функция редактирования в разработке.\nПока можно удалить книгу и добавить заново.",
        show_alert=True
    )