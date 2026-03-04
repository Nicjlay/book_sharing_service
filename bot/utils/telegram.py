"""
utils/telegram.py — утилиты для работы с Telegram сообщениями.
"""
import asyncio
import logging
import os
import re
from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, Message

logger = logging.getLogger(__name__)

MEDIA_ROOT = os.getenv("MEDIA_UPLOAD_DIR", "/app/media")

# Telegram ограничивает caption 1024 символами
_TELEGRAM_CAPTION_LIMIT = 1024

# Telegram ограничивает text сообщений 4096 символами
_TELEGRAM_TEXT_LIMIT = 4096

# Регулярное выражение для удаления незакрытого HTML-тега в конце строки.
# После наивного обрезания «<b>Текст…» может остаться «<b>Текс» — открытый тег
# без закрывающего «>». Telegram отклоняет такой HTML с TelegramBadRequest.
# Паттерн удаляет последовательность «<» до конца строки если нет парного «>».
_INCOMPLETE_TAG_RE = re.compile(r"<[^>]*$")


def _safe_html_truncate(text: str, max_len: int, suffix: str = "…") -> str:
    """
    Обрезает HTML-текст до max_len символов с сохранением структурной
    целостности тегов.

    Проблема наивного text[:N]:
    - «<b>Длинный текст</b>» → «<b>Длинны» — незакрытый тег → TelegramBadRequest
    - «Текст с <a href="...»  → обрыв внутри атрибута → TelegramBadRequest

    Решение:
    1. Обрезаем по символам (суффикс оставляет место для «…»)
    2. Удаляем незакрытый тег-фрагмент на конце _INCOMPLETE_TAG_RE

    Примечание: незакрытые парные теги типа «<b>текст» без «</b>» не исправляются —
    Telegram принимает «мягко сломанный» HTML в этом случае без ошибки.
    """
    if len(text) <= max_len:
        return text
    truncated = text[: max_len - len(suffix)] + suffix
    return _INCOMPLETE_TAG_RE.sub("", truncated)


def _is_safe_image_path(image_path: str) -> bool:
    """
    Проверка пути изображения на безопасность (защита от path traversal).

    Используем os.path.normpath для нормализации пути перед проверкой:
    после нормализации все «../» коллапсируются и мы проверяем что финальный
    путь начинается с MEDIA_ROOT.
    """
    if not image_path:
        return False
    norm_root = os.path.normpath(os.path.abspath(MEDIA_ROOT))
    full_path = os.path.normpath(os.path.join(MEDIA_ROOT, image_path))
    return full_path.startswith(norm_root + os.sep) or full_path == norm_root


async def get_photo_input(image_path: str) -> Optional[BufferedInputFile]:
    """
    Читает обложку книги: сначала с диска (asyncio.to_thread), потом фолбек на API.

    asyncio.to_thread предотвращает блокировку event loop при файловом I/O.
    Lazy import api во избежание circular dependency (api.client → telegram → api.client).
    """
    from api.client import api  # noqa: PLC0415

    if not _is_safe_image_path(image_path):
        logger.warning("Rejected suspicious image_path: %r", image_path)
        return None

    full_path = os.path.join(MEDIA_ROOT, image_path)

    def _read_file() -> Optional[bytes]:
        if os.path.exists(full_path):
            with open(full_path, "rb") as f:
                return f.read()
        return None

    file_data = await asyncio.to_thread(_read_file)
    if file_data:
        return BufferedInputFile(file_data, filename=os.path.basename(full_path))

    # Фолбек: скачиваем через API
    photo_bytes = await api.get_image_bytes(image_path)
    if photo_bytes:
        return BufferedInputFile(photo_bytes, filename=os.path.basename(image_path))

    return None


async def safe_edit_message(
    message: Message,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    """
    Универсальное редактирование сообщения без ошибок на фото-карточках.

    Telegram хранит photo-сообщения без поля text (только caption).
    edit_text() на таком сообщении → «there is no text in the message to edit».
    Определяем тип и выбираем нужный метод.

    Telegram ограничивает caption 1024 символами и text 4096 символами.
    При превышении используем _safe_html_truncate: наивное text[:N] разрывает
    HTML-теги в середине → TelegramBadRequest с «can't parse entities».
    """
    try:
        if message.photo or message.document or message.sticker or message.video:
            # Caption limit: 1024 символа — HTML-безопасное усечение
            caption = _safe_html_truncate(text, _TELEGRAM_CAPTION_LIMIT)
            await message.edit_caption(
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            # Text limit: 4096 символов — HTML-безопасное усечение
            safe_text = _safe_html_truncate(text, _TELEGRAM_TEXT_LIMIT)
            await message.edit_text(
                safe_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    except TelegramBadRequest as e:
        err = str(e).lower()
        # «message is not modified» — контент не изменился, не страшно
        # «message to edit not found» — сообщение удалено, не страшно
        # «message can't be edited» — форум-треды / старые сообщения, не страшно
        if "not modified" in err or "not found" in err or "can't be edited" in err:
            return
        raise


async def safe_delete_message(message: Message) -> None:
    """
    Удаляет сообщение, игнорируя ожидаемые ошибки (уже удалено / нет прав).
    Логирует только неожиданные ошибки.
    """
    try:
        await message.delete()
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message to delete not found" in err or "message can't be deleted" in err:
            return
        logger.warning("Unexpected error deleting message: %s", e)
    except Exception as e:
        logger.warning("Error deleting message: %s", e)
