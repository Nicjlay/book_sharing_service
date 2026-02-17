"""
Handler для команды /start и главного меню
"""
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from api.client import api
from keyboards.inline import main_menu_keyboard
from config import settings

router = Router()


async def safe_edit_message(message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """
    Универсальное редактирование сообщения.
    Карточки книг с фото — это photo-сообщения, у них нет text, только caption.
    edit_text() на них → Bad Request: there is no text in the message to edit.
    """
    if message.photo or message.document or message.sticker:
        await message.edit_caption(
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    else:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """
    Обработка команды /start
    Регистрация/авторизация пользователя в системе
    """
    # Очищаем состояние на всякий случай
    await state.clear()

    # Авторизация пользователя в API
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    username = message.from_user.username

    # Проверяем, является ли пользователь админом
    is_admin = user_id in settings.admin_ids_list

    try:
        # Регистрируем/обновляем пользователя в API
        await api.auth_user(
            tg_id=user_id,
            full_name=full_name,
            username=username,
            is_admin=int(is_admin)
        )

        # Приветственное сообщение
        welcome_text = (
            f"👋 Привет, {full_name}!\n\n"
            f"📚 Добро пожаловать в <b>Библиотеку</b> — бот для обмена и учёта книг.\n\n"
            f"Здесь вы можете:\n"
            f"• 📖 Просматривать каталог книг\n"
            f"• 🔍 Искать интересующие книги\n"
            f"• 🟢 Бронировать доступные книги\n"
            f"• 📚 Управлять своими книгами\n"
        )

        if is_admin:
            welcome_text += (
                f"\n👨‍💼 <b>Вы администратор</b>\n"
                f"• ➕ Добавлять новые книги\n"
                f"• ✅ Подтверждать заявки на бронирование\n"
                f"• 📋 Управлять каталогом\n"
            )

        welcome_text += "\nВыберите действие:"

        await message.answer(
            welcome_text,
            reply_markup=main_menu_keyboard(is_admin=is_admin),
            parse_mode="HTML"
        )

    except Exception as e:
        await message.answer(
            "❌ Ошибка подключения к серверу. Попробуйте позже.",
            parse_mode="HTML"
        )
        print(f"Error in /start: {e}")


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    """
    Команда /menu - вернуться в главное меню
    """
    await state.clear()

    user_id = message.from_user.id
    is_admin = user_id in settings.admin_ids_list

    await message.answer(
        "📋 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(is_admin=is_admin),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню через inline кнопку"""
    await state.clear()

    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_list

    await safe_edit_message(
        callback.message,
        "📋 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(is_admin=is_admin)
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустой callback для неактивных кнопок"""
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    """Отмена текущего действия"""
    await state.clear()

    user_id = callback.from_user.id
    is_admin = user_id in settings.admin_ids_list

    await safe_edit_message(
        callback.message,
        "❌ Действие отменено\n\n📋 <b>Главное меню</b>",
        reply_markup=main_menu_keyboard(is_admin=is_admin)
    )
    await callback.answer("Отменено")


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам"""
    help_text = (
        "📖 <b>Доступные команды:</b>\n\n"
        "/start - Начать работу с ботом\n"
        "/menu - Главное меню\n"
        "/catalog - Открыть каталог книг\n"
        "/search - Поиск книг\n"
        "/mybooks - Мои книги\n"
        "/help - Эта справка\n"
    )

    if message.from_user.id in settings.admin_ids_list:
        help_text += (
            "\n👨‍💼 <b>Команды администратора:</b>\n"
            "/add - Добавить книгу\n"
            "/admin - Админ панель\n"
        )

    help_text += (
        "\n💡 <b>Подсказка:</b>\n"
        "Используйте кнопки для навигации по боту.\n"
        "В любой момент можете вернуться в главное меню командой /menu"
    )

    await message.answer(help_text, parse_mode="HTML")