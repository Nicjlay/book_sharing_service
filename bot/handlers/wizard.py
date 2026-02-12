"""
Handler для визарда добавления книги (ТЗ 3.1)
Пошаговый диалог из 6 шагов
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from api.client import api
from states.wizard import AddBookWizard
from keyboards.inline import (
    wizard_skip_photo_keyboard,
    genres_keyboard,
    users_selection_keyboard,
    wizard_confirm_keyboard,
    cancel_keyboard
)
from utils.validators import validate_author, validate_title, validate_description, is_image
from utils.formatters import format_wizard_preview
from config import settings

router = Router()


@router.message(Command("add"))
@router.callback_query(F.data == "add_book")
async def start_add_book_wizard(event: Message | CallbackQuery, state: FSMContext):
    """
    Начало визарда добавления книги
    Шаг 1: Запрос автора (ТЗ 3.1.1)
    """
    user_id = event.from_user.id if isinstance(event, Message) else event.message.from_user.id
    
    # Проверка прав (только админы могут добавлять книги)
    if user_id not in settings.admin_ids_list:
        text = "❌ Добавлять книги могут только администраторы."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return
    
    # Начинаем визард
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


@router.message(AddBookWizard.author)
async def process_author(message: Message, state: FSMContext):
    """
    Обработка ввода автора с валидацией (ТЗ 3.1.1)
    """
    author = message.text.strip()
    
    # Валидация: минимум два слова
    is_valid, error_msg = validate_author(author)
    
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return
    
    # Сохраняем автора и переходим к следующему шагу
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["author"] = author
    await state.update_data(wizard_data=wizard_data)
    
    # Шаг 2: Название книги
    await state.set_state(AddBookWizard.title)
    
    await message.answer(
        f"✅ Автор: <b>{author}</b>\n\n"
        f"📝 <b>Шаг 2/6:</b> Название книги\n\n"
        f"Введите полное название книги:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )


@router.message(AddBookWizard.title)
async def process_title(message: Message, state: FSMContext):
    """
    Обработка ввода названия книги (ТЗ 3.1.2)
    """
    title = message.text.strip()
    
    # Валидация
    is_valid, error_msg = validate_title(title)
    
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return
    
    # Сохраняем название
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["title"] = title
    await state.update_data(wizard_data=wizard_data)
    
    # Шаг 3: Фото обложки (опционально)
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


@router.callback_query(F.data == "skip_photo", AddBookWizard.photo)
async def skip_photo(callback: CallbackQuery, state: FSMContext):
    """
    Пропуск загрузки фото (ТЗ 3.1.3)
    """
    # Переходим к следующему шагу без фото
    await process_photo_step_complete(callback.message, state, photo_path=None)
    await callback.answer("Фото пропущено")


@router.message(AddBookWizard.photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    """
    Обработка загруженного фото (ТЗ 3.1.3)
    """
    # Получаем самое большое фото
    photo = message.photo[-1]
    
    # Проверка размера (до 5 МБ)
    if photo.file_size > 5 * 1024 * 1024:
        await message.answer(
            "❌ Файл слишком большой (максимум 5 МБ)\n"
            "Попробуйте загрузить другое фото или пропустите этот шаг.",
            parse_mode="HTML"
        )
        return
    
    try:
        # Скачиваем фото
        file = await message.bot.download(photo.file_id)
        file_data = file.read()
        
        # Загружаем на сервер через API
        image_path = await api.upload_media(file_data, f"book_{photo.file_id}.jpg")
        
        # Сохраняем путь к фото
        data = await state.get_data()
        wizard_data = data.get("wizard_data", {})
        wizard_data["image_path"] = image_path
        await state.update_data(wizard_data=wizard_data)
        
        await message.answer("✅ Фото обложки загружено!")
        await process_photo_step_complete(message, state, photo_path=image_path)
        
    except Exception as e:
        await message.answer(
            "❌ Ошибка загрузки фото. Попробуйте еще раз или пропустите этот шаг.",
            parse_mode="HTML"
        )
        print(f"Error uploading photo: {e}")


@router.message(AddBookWizard.photo, F.document)
async def process_document_as_photo(message: Message, state: FSMContext):
    """
    Обработка документа как фото (если пользователь отправил изображение как документ)
    """
    document = message.document
    
    # Проверяем, что это изображение
    if not is_image(document.file_name):
        await message.answer(
            "❌ Пожалуйста, отправьте изображение (JPG, PNG, WEBP)",
            parse_mode="HTML"
        )
        return
    
    # Проверка размера
    if document.file_size > 5 * 1024 * 1024:
        await message.answer(
            "❌ Файл слишком большой (максимум 5 МБ)",
            parse_mode="HTML"
        )
        return
    
    try:
        # Скачиваем и загружаем
        file = await message.bot.download(document.file_id)
        file_data = file.read()
        
        image_path = await api.upload_media(file_data, document.file_name)
        
        data = await state.get_data()
        wizard_data = data.get("wizard_data", {})
        wizard_data["image_path"] = image_path
        await state.update_data(wizard_data=wizard_data)
        
        await message.answer("✅ Фото обложки загружено!")
        await process_photo_step_complete(message, state, photo_path=image_path)
        
    except Exception as e:
        await message.answer("❌ Ошибка загрузки фото")
        print(f"Error uploading document: {e}")


@router.message(AddBookWizard.photo)
async def invalid_photo_format(message: Message):
    """
    Обработка неправильного формата на шаге фото
    """
    await message.answer(
        "❌ Пожалуйста, отправьте <b>изображение</b> или нажмите кнопку «Пропустить»",
        reply_markup=wizard_skip_photo_keyboard(),
        parse_mode="HTML"
    )


async def process_photo_step_complete(message: Message, state: FSMContext, photo_path: str = None):
    """
    Завершение шага с фото, переход к Шагу 4: Описание и жанр
    """
    # Шаг 4: Описание и жанр (ТЗ 3.1.4)
    await state.set_state(AddBookWizard.description)
    
    # Получаем список жанров для кнопок
    try:
        genres = await api.get_genres()
        
        # Создаем клавиатуру с жанрами
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        
        builder = InlineKeyboardBuilder()
        
        # Кнопки частых жанров (ТЗ 3.1.4)
        common_genres = ["Роман", "Фантастика", "Non-fiction", "Бизнес", "Психология"]
        for genre in common_genres:
            if genre in genres:
                builder.row(
                    InlineKeyboardButton(text=f"📚 {genre}", callback_data=f"select_genre:{genre}")
                )
        
        # Кнопка "Другое" для ввода своего жанра
        builder.row(
            InlineKeyboardButton(text="✏️ Ввести свой жанр", callback_data="select_genre:custom")
        )
        
        # Кнопка пропуска
        builder.row(
            InlineKeyboardButton(text="⏭ Пропустить описание и жанр", callback_data="skip_description")
        )
        
        builder.row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="wizard_cancel")
        )
        
        await message.answer(
            f"📝 <b>Шаг 4/6:</b> Описание и жанр\n\n"
            f"Выберите жанр из списка или введите краткое описание книги:\n\n"
            f"Можно пропустить этот шаг",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        print(f"Error getting genres: {e}")
        await message.answer(
            f"📝 <b>Шаг 4/6:</b> Описание и жанр\n\n"
            f"Введите краткое описание книги или жанр:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("select_genre:"), AddBookWizard.description)
async def select_genre(callback: CallbackQuery, state: FSMContext):
    """
    Выбор жанра из предложенных (ТЗ 3.1.4)
    """
    genre = callback.data.split(":", 1)[1]
    
    if genre == "custom":
        # Пользователь хочет ввести свой жанр
        await callback.message.edit_text(
            "📝 Введите название жанра:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Сохраняем выбранный жанр
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["genre"] = genre
    await state.update_data(wizard_data=wizard_data)
    
    await callback.answer(f"Выбран жанр: {genre}")
    
    # Переходим к выбору владельца
    await process_description_complete(callback.message, state)


@router.callback_query(F.data == "skip_description", AddBookWizard.description)
async def skip_description(callback: CallbackQuery, state: FSMContext):
    """
    Пропуск описания и жанра
    """
    await callback.answer("Описание и жанр пропущены")
    await process_description_complete(callback.message, state)


@router.message(AddBookWizard.description)
async def process_description(message: Message, state: FSMContext):
    """
    Обработка ввода описания (ТЗ 3.1.4)
    """
    text = message.text.strip()
    
    # Валидация
    is_valid, error_msg = validate_description(text)
    
    if not is_valid:
        await message.answer(error_msg, parse_mode="HTML")
        return
    
    # Сохраняем описание
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    wizard_data["description"] = text
    await state.update_data(wizard_data=wizard_data)
    
    await message.answer("✅ Описание сохранено")
    await process_description_complete(message, state)


async def process_description_complete(message: Message, state: FSMContext):
    """
    Завершение шага описания, переход к Шагу 5: Выбор владельца
    """
    # Шаг 5: Выбор владельца (ТЗ 3.1.5)
    await state.set_state(AddBookWizard.owner)
    
    try:
        # Получаем список пользователей
        users = await api.search_users()
        
        if not users:
            await message.answer(
                "❌ Не найдено зарегистрированных пользователей.\n"
                "Попросите пользователей запустить бота командой /start",
                parse_mode="HTML"
            )
            return
        
        await message.answer(
            f"📝 <b>Шаг 5/6:</b> Владелец книги\n\n"
            f"Выберите владельца книги из списка:\n\n"
            f"💡 Или введите username/имя для поиска",
            reply_markup=users_selection_keyboard(users),
            parse_mode="HTML"
        )
        
        # Сохраняем список пользователей для поиска
        await state.update_data(all_users=users)
        
    except Exception as e:
        await message.answer("❌ Ошибка загрузки пользователей")
        print(f"Error loading users: {e}")


@router.message(AddBookWizard.owner)
async def search_owner(message: Message, state: FSMContext):
    """
    Поиск владельца по username или имени (ТЗ 3.1.5)
    """
    query = message.text.strip()
    
    if len(query) < 2:
        await message.answer(
            "❌ Запрос слишком короткий. Введите минимум 2 символа.",
            parse_mode="HTML"
        )
        return
    
    try:
        # Ищем пользователей
        users = await api.search_users(query)
        
        if not users:
            await message.answer(
                f"🔍 По запросу <b>«{query}»</b> никто не найден.\n\n"
                f"Попробуйте другой запрос:",
                parse_mode="HTML"
            )
            return
        
        await message.answer(
            f"🔍 Результаты поиска по <b>«{query}»</b>:\n\n"
            f"Выберите владельца:",
            reply_markup=users_selection_keyboard(users),
            parse_mode="HTML"
        )
        
    except Exception as e:
        await message.answer("❌ Ошибка поиска")
        print(f"Error searching users: {e}")


@router.callback_query(F.data.startswith("select_owner:"), AddBookWizard.owner)
async def select_owner(callback: CallbackQuery, state: FSMContext):
    """
    Выбор владельца книги (ТЗ 3.1.5)
    """
    owner_id = int(callback.data.split(":")[1])
    
    # Получаем данные владельца для отображения
    try:
        data = await state.get_data()
        all_users = data.get("all_users", [])
        
        # Ищем выбранного пользователя
        owner = None
        for user in all_users:
            if user["id"] == owner_id:
                owner = user
                break
        
        # Если не нашли в кэше, запрашиваем через поиск
        if not owner:
            users = await api.search_users()
            for user in users:
                if user["id"] == owner_id:
                    owner = user
                    break
        
        if not owner:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        owner_name = owner.get("username") or owner.get("full_name", "Неизвестный")
        
        # Сохраняем владельца
        wizard_data = data.get("wizard_data", {})
        wizard_data["owner_id"] = owner_id
        wizard_data["owner_name"] = owner_name
        await state.update_data(wizard_data=wizard_data)
        
        await callback.answer(f"Выбран: {owner_name}")
        
        # Шаг 6: Подтверждение (ТЗ 3.1.6)
        await process_owner_selected(callback.message, state)
        
    except Exception as e:
        await callback.answer("Ошибка выбора владельца", show_alert=True)
        print(f"Error selecting owner: {e}")


async def process_owner_selected(message: Message, state: FSMContext):
    """
    Переход к шагу 6: Подтверждение (ТЗ 3.1.6)
    """
    await state.set_state(AddBookWizard.confirm)
    
    # Получаем все данные
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    
    # Формируем превью
    preview_text = format_wizard_preview(wizard_data)
    
    # Если есть фото, показываем с фото
    if wizard_data.get("image_path"):
        try:
            image_url = f"{settings.api_url}/{wizard_data['image_path']}"
            
            await message.answer_photo(
                photo=image_url,
                caption=preview_text,
                reply_markup=wizard_confirm_keyboard(),
                parse_mode="HTML"
            )
        except:
            # Если фото не загрузилось
            await message.answer(
                preview_text,
                reply_markup=wizard_confirm_keyboard(),
                parse_mode="HTML"
            )
    else:
        await message.answer(
            preview_text,
            reply_markup=wizard_confirm_keyboard(),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "wizard_confirm", AddBookWizard.confirm)
async def confirm_and_create_book(callback: CallbackQuery, state: FSMContext):
    """
    Подтверждение и создание книги (ТЗ 3.1.6)
    """
    data = await state.get_data()
    wizard_data = data.get("wizard_data", {})
    
    try:
        # Отправляем данные на API
        book_data = {
            "title": wizard_data["title"],
            "author": wizard_data["author"],
            "owner_id": wizard_data["owner_id"],
            "description": wizard_data.get("description"),
            "genre": wizard_data.get("genre", "Другое"),
            "image_path": wizard_data.get("image_path")
        }
        
        # Создаем книгу
        book = await api.create_book(book_data)
        
        # Очищаем состояние
        await state.clear()
        
        # Успешное добавление
        await callback.message.edit_text(
            f"✅ <b>Книга успешно добавлена!</b>\n\n"
            f"📖 {book['title']}\n"
            f"✍️ {book['author']}\n"
            f"🔖 ID: #{book['id']:05d}\n\n"
            f"Книга появится в каталоге",
            parse_mode="HTML"
        )
        
        await callback.answer("Книга добавлена!", show_alert=True)
        
        # Отправляем уведомление в группу (если настроена)
        # API само отправит уведомление через webhook
        
    except Exception as e:
        await callback.answer("Ошибка создания книги", show_alert=True)
        await callback.message.answer(
            "❌ Произошла ошибка при добавлении книги.\n"
            "Попробуйте еще раз позже.",
            parse_mode="HTML"
        )
        print(f"Error creating book: {e}")


@router.callback_query(F.data == "wizard_edit", AddBookWizard.confirm)
async def edit_wizard_data(callback: CallbackQuery, state: FSMContext):
    """
    Возврат к редактированию данных
    """
    await callback.message.edit_text(
        "✏️ <b>Редактирование</b>\n\n"
        "Выберите, что хотите изменить:\n\n"
        "1️⃣ Автор\n"
        "2️⃣ Название\n"
        "3️⃣ Фото\n"
        "4️⃣ Описание/Жанр\n"
        "5️⃣ Владелец\n\n"
        "Введите номер (1-5):",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    # TODO: Можно добавить полноценное редактирование, но это усложнит код
    # Пока просто отменяем
    await callback.answer("Функция в разработке. Пожалуйста, начните заново.")


@router.callback_query(F.data == "wizard_cancel")
async def cancel_wizard(callback: CallbackQuery, state: FSMContext):
    """
    Отмена визарда
    """
    await state.clear()
    
    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_list
    
    from keyboards.inline import main_menu_keyboard
    
    await callback.message.edit_text(
        "❌ Добавление книги отменено\n\n📋 Главное меню",
        reply_markup=main_menu_keyboard(is_admin),
        parse_mode="HTML"
    )
    await callback.answer("Отменено")
