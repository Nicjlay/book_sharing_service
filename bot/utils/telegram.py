"""
utils/telegram.py — общие утилиты для работы с Telegram сообщениями.

Размести этот файл в папке utils/ и импортируй везде:
    from utils.telegram import safe_edit_message
"""
from aiogram.types import Message


async def safe_edit_message(
    message: Message,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML"
) -> None:
    """
    Универсальное редактирование сообщения без ошибок на фото-карточках.

    Проблема: карточки книг с обложкой отправляются через answer_photo().
    Telegram хранит их как photo-сообщения — поле text отсутствует, только caption.
    Вызов edit_text() на таком сообщении → Bad Request: there is no text in the message to edit.

    Решение: определяем тип сообщения и используем нужный метод:
    - фото/документ/стикер → edit_caption()
    - обычный текст       → edit_text()
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
