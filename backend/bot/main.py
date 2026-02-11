from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FContext
from states import AddBookWizard

router = Router()


@router.message(Command("add_book"))
async def start_add_book(message: types.Message, state: FSMContext):
    await message.answer("Шаг 1: Введите фамилию и имя автора (например, Достоевский Федор).")
    await state.set_state(AddBookWizard.author)


@router.message(AddBookWizard.author)
async def process_author(message: types.Message, state: FSMContext):
    # Проверка из ТЗ: если введено одно слово
    words = message.text.split()
    if len(words) < 2:
        await message.answer("⚠️ Уточните имя автора. Нужно как минимум два слова (Фамилия и Имя).")
        return

    await state.update_data(author=message.text)
    await message.answer("Шаг 2: Введите полное название книги.")
    await state.set_state(AddBookWizard.title)


@router.message(AddBookWizard.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Шаг 3: Загрузите фотографию обложки (до 5 МБ) или /skip.")
    await state.set_state(AddBookWizard.photo)