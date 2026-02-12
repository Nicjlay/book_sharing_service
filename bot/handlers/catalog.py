"""
Handler для просмотра каталога книг (ТЗ 3.2.1)
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from api.client import api
from keyboards.inline import (
    catalog_filters_keyboard,
    genres_keyboard,
    book_card_keyboard,
    main_menu_keyboard,
    pagination_keyboard
)
from utils.formatters import format_book_card, format_book_list
from config import settings
from states.wizard import SearchStates

router = Router()


@router.message(Command("catalog"))
@router.callback_query(F.data == "catalog")
async def show_catalog(event: Message | CallbackQuery, state: FSMContext):
    """
    Открыть каталог с фильтрами (ТЗ 3.2.1)
    """
    await state.clear()
    
    text = (
        "📚 <b>Каталог книг</b>\n\n"
        "Выберите фильтр для просмотра книг:"
    )
    
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(
            text,
            reply_markup=catalog_filters_keyboard(),
            parse_mode="HTML"
        )
        await event.answer()
    else:
        await event.answer(
            text,
            reply_markup=catalog_filters_keyboard(),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("filter:"))
async def apply_filter(callback: CallbackQuery, state: FSMContext):
    """
    Применить фильтр к каталогу
    """
    filter_type = callback.data.split(":")[1]
    
    try:
        if filter_type == "all":
            # Все книги
            books = await api.get_books()
            title = "📚 Все книги"
            
        elif filter_type == "available":
            # Только доступные
            books = await api.get_books(status="available")
            title = "🟢 Доступные книги"
            
        elif filter_type == "genres":
            # Показываем список жанров
            genres = await api.get_genres()
            await callback.message.edit_text(
                "📖 <b>Выберите жанр:</b>",
                reply_markup=genres_keyboard(genres),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        else:
            await callback.answer("Неизвестный фильтр", show_alert=True)
            return
        
        if not books:
            await callback.message.edit_text(
                f"{title}\n\n📭 Книги не найдены",
                reply_markup=catalog_filters_keyboard(),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        # Сохраняем книги в состояние для пагинации
        await state.update_data(books=books, current_page=0, filter_title=title)
        
        # Показываем первую страницу
        await show_books_page(callback.message, books, 0, title, state)
        await callback.answer()
        
    except Exception as e:
        await callback.answer("Ошибка загрузки каталога", show_alert=True)
        print(f"Error in apply_filter: {e}")


@router.callback_query(F.data.startswith("genre:"))
async def filter_by_genre(callback: CallbackQuery, state: FSMContext):
    """
    Фильтр по жанру
    """
    genre = callback.data.split(":", 1)[1]
    
    try:
        books = await api.get_books(genre=genre)
        title = f"📚 Жанр: {genre}"
        
        if not books:
            await callback.message.edit_text(
                f"{title}\n\n📭 Книги не найдены",
                reply_markup=catalog_filters_keyboard(),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        await state.update_data(books=books, current_page=0, filter_title=title)
        await show_books_page(callback.message, books, 0, title, state)
        await callback.answer()
        
    except Exception as e:
        await callback.answer("Ошибка загрузки", show_alert=True)
        print(f"Error in filter_by_genre: {e}")


async def show_books_page(message: Message, books: list, page: int, title: str, state: FSMContext):
    """
    Показать страницу с книгами и кнопками для детального просмотра
    """
    per_page = 5
    start = page * per_page
    end = start + per_page
    page_books = books[start:end]
    
    text = f"{title}\n\n"
    text += f"📄 Найдено: {len(books)} книг\n"
    text += f"📄 Страница: {page + 1} / {(len(books) - 1) // per_page + 1}\n\n"
    
    # Список книг на странице
    status_emoji = {
        "available": "🟢",
        "reserved": "🟡",
        "borrowed": "🔴",
        "overdue": "⏳"
    }
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    
    builder = InlineKeyboardBuilder()
    
    for i, book in enumerate(page_books, 1):
        emoji = status_emoji.get(book.get("status", "available"), "⚪️")
        text += f"{emoji} <b>{book['title']}</b>\n"
        text += f"   ✍️ {book['author']}\n"
        text += f"   🔖 ID: #{book['id']:05d}\n\n"
        
        # Кнопка для просмотра деталей
        builder.row(
            InlineKeyboardButton(
                text=f"📖 {book['title'][:30]}...",
                callback_data=f"book:{book['id']}"
            )
        )
    
    # Пагинация
    total_pages = (len(books) - 1) // per_page + 1
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page:{page - 1}")
            )
        nav_buttons.append(
            InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(text="➡️ Вперед", callback_data=f"page:{page + 1}")
            )
        builder.row(*nav_buttons)
    
    # Кнопки навигации
    builder.row(
        InlineKeyboardButton(text="🔙 К фильтрам", callback_data="catalog")
    )
    
    await message.edit_text(
        text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("page:"))
async def change_page(callback: CallbackQuery, state: FSMContext):
    """
    Пагинация - переход на другую страницу
    """
    page = int(callback.data.split(":")[1])
    data = await state.get_data()
    
    books = data.get("books", [])
    title = data.get("filter_title", "📚 Книги")
    
    await state.update_data(current_page=page)
    await show_books_page(callback.message, books, page, title, state)
    await callback.answer()


@router.callback_query(F.data.startswith("book:"))
async def show_book_detail(callback: CallbackQuery):
    """
    Показать детальную карточку книги
    """
    book_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_list
    
    try:
        book = await api.get_book(book_id)
        
        if not book:
            await callback.answer("Книга не найдена", show_alert=True)
            return
        
        # Форматируем карточку
        text = format_book_card(book)
        
        # Если есть фото - отправляем с фото
        if book.get("image_path"):
            try:
                # Формируем URL изображения
                image_url = f"{settings.api_url}/{book['image_path']}"
                
                await callback.message.delete()
                await callback.message.answer_photo(
                    photo=image_url,
                    caption=text,
                    reply_markup=book_card_keyboard(book, user_id, is_admin),
                    parse_mode="HTML"
                )
            except:
                # Если фото не загрузилось, показываем без фото
                await callback.message.edit_text(
                    text,
                    reply_markup=book_card_keyboard(book, user_id, is_admin),
                    parse_mode="HTML"
                )
        else:
            await callback.message.edit_text(
                text,
                reply_markup=book_card_keyboard(book, user_id, is_admin),
                parse_mode="HTML"
            )
        
        await callback.answer()
        
    except Exception as e:
        await callback.answer("Ошибка загрузки книги", show_alert=True)
        print(f"Error in show_book_detail: {e}")


@router.message(Command("search"))
@router.callback_query(F.data == "search")
async def start_search(event: Message | CallbackQuery, state: FSMContext):
    """
    Начать поиск книг (ТЗ 4.3)
    """
    await state.set_state(SearchStates.query)
    
    text = (
        "🔍 <b>Поиск книг</b>\n\n"
        "Введите название книги или имя автора для поиска:\n\n"
        "Например: <i>Достоевский</i> или <i>Идиот</i>"
    )
    
    from keyboards.inline import cancel_keyboard
    
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(
            text,
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        await event.answer()
    else:
        await event.answer(
            text,
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )


@router.message(SearchStates.query)
async def process_search(message: Message, state: FSMContext):
    """
    Обработка поискового запроса
    """
    query = message.text.strip()
    
    if len(query) < 2:
        await message.answer(
            "❌ Запрос слишком короткий. Введите минимум 2 символа.",
            parse_mode="HTML"
        )
        return
    
    try:
        books = await api.get_books(query=query)
        
        if not books:
            await message.answer(
                f"🔍 По запросу <b>«{query}»</b> ничего не найдено.\n\n"
                f"Попробуйте другой запрос:",
                parse_mode="HTML"
            )
            return
        
        # Показываем результаты
        await state.clear()
        await state.update_data(books=books, current_page=0, filter_title=f"🔍 Поиск: {query}")
        
        # Отправляем новое сообщение с результатами
        temp_msg = await message.answer("⏳ Поиск...")
        await show_books_page(temp_msg, books, 0, f"🔍 Результаты поиска: {query}", state)
        
    except Exception as e:
        await message.answer("❌ Ошибка поиска. Попробуйте еще раз.")
        print(f"Error in process_search: {e}")
