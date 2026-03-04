"""
Webhook endpoint для приема push-уведомлений от API
"""
import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Dict, Optional

import httpx
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel

from config import settings
from utils.formatters import format_notification

logger = logging.getLogger(__name__)

# Максимальный размер тела webhook-запроса (1 МБ).
# Защищает от payload-flooding атак без установки реального rate limiting.
_MAX_PAYLOAD_BYTES = 1024 * 1024

# Максимальный размер скачиваемого фото (10 МБ).
# Совпадает с лимитом в api/client.py для единообразия.
_MAX_PHOTO_BYTES = 10 * 1024 * 1024

# Размер чанка при потоковом чтении фото
_PHOTO_CHUNK_SIZE = 65_536  # 64 КБ

# Telegram ограничивает caption 1024 символами
_TELEGRAM_CAPTION_LIMIT = 1024


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом FastAPI."""
    yield
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        logger.info("Webhook HTTP client closed")


app = FastAPI(title="Library Bot Webhook", lifespan=lifespan)

bot: Optional[Bot] = None


def set_bot(bot_instance: Bot):
    """Установить экземпляр бота"""
    global bot
    bot = bot_instance


class NotificationPayload(BaseModel):
    """Формат уведомления от API"""
    user_id: int      # -1 = всем админам, 0 = в группу
    type: str
    message: str
    book_id: Optional[int] = None
    meta: Dict = {}


# Singleton HTTP client + asyncio lock для потокобезопасного создания.
_http_client: Optional[httpx.AsyncClient] = None
_http_client_lock = asyncio.Lock()


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(timeout=10.0)
    return _http_client


def _is_safe_media_path(photo_path: str) -> bool:
    """
    Проверка пути к фото на безопасность (защита от path traversal и SSRF).

    Требования к «безопасному» пути для подстановки в URL:
    - Не абсолютный (не начинается с / или \\)
    - После нормализации не выходит за пределы текущей «директории»
      (нормализованный путь не начинается с «..»)
    - Не содержит схему URL (защита от «javascript:», «file:» и пр.)

    os.path.normpath корректно обрабатывает «/», «\» и URL-encoded сегменты
    на уровне строки (не декодирует %2e%2e — это задача HTTP-сервера).
    Для дополнительной защиты фильтруем «%» — если путь URL-encoded,
    его должен декодировать и валидировать сервер API, а не мы.
    """
    if not photo_path:
        return False
    if photo_path.startswith("/") or photo_path.startswith("\\"):
        return False
    if "%" in photo_path or ":" in photo_path:
        return False
    normalized = os.path.normpath(photo_path)
    return not normalized.startswith("..")


async def _fetch_photo(photo_path: str) -> Optional[bytes]:
    """
    Скачивает фото с API по внутреннему пути.

    1. Добавлен заголовок X-API-Token — без него защищённые эндпоинты
       возвращают 401/403 и фото в уведомлениях не отображались.
    2. Потоковое чтение (stream) с жёстким лимитом _MAX_PHOTO_BYTES —
       защита от OOM при загрузке большого файла.
    3. Быстрая проверка Content-Length до начала загрузки.
    """
    if not photo_path or not _is_safe_media_path(photo_path):
        if photo_path:
            logger.warning("Rejected suspicious photo_path: %r", photo_path)
        return None

    try:
        api_url = settings.api_url.rstrip("/")
        url = f"{api_url}/media/{photo_path}"
        client = await _get_http_client()

        async with client.stream(
            "GET",
            url,
            headers={"X-API-Token": settings.api_token},
        ) as resp:
            if resp.status_code != 200:
                logger.warning(
                    "Failed to fetch photo %s: HTTP %d", photo_path, resp.status_code
                )
                return None

            # Быстрая проверка по Content-Length до начала загрузки
            content_length_raw = resp.headers.get("content-length")
            if content_length_raw:
                try:
                    if int(content_length_raw) > _MAX_PHOTO_BYTES:
                        logger.warning(
                            "Photo %s rejected: Content-Length=%s exceeds %d bytes",
                            photo_path, content_length_raw, _MAX_PHOTO_BYTES,
                        )
                        return None
                except ValueError:
                    pass  # Некорректный заголовок — продолжаем, проверим по факту

            # Потоковое чтение с жёстким лимитом по реальному размеру
            chunks: list[bytes] = []
            total_size = 0
            async for chunk in resp.aiter_bytes(_PHOTO_CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > _MAX_PHOTO_BYTES:
                    logger.warning(
                        "Photo %s aborted: exceeded %d bytes (got %d so far)",
                        photo_path, _MAX_PHOTO_BYTES, total_size,
                    )
                    return None
                chunks.append(chunk)

            return b"".join(chunks)

    except Exception as e:
        logger.warning("Failed to fetch photo %s: %s", photo_path, e)
    return None


def _make_reservation_keyboard(book_id: int):
    """Кнопки быстрого действия для уведомления о заявке."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Выдать книгу", callback_data=f"approve:{book_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{book_id}"),
    )
    return builder.as_markup()


async def _send_to_user(
    user_id: int,
    text: str,
    photo_path: Optional[str] = None,
    reply_markup=None,
):
    """
    Отправляет уведомление пользователю.

    Если есть фото — отправляем как send_photo с текстом в caption.
    При caption > 1024 символов: фото + обрезанный caption, затем полный
    текст с reply_markup (кнопки всегда в последнем сообщении).
    """
    photo_bytes: Optional[bytes] = None
    if photo_path:
        photo_bytes = await _fetch_photo(photo_path)

    if photo_bytes:
        try:
            photo_file = BufferedInputFile(photo_bytes, filename="photo.jpg")
            if len(text) <= _TELEGRAM_CAPTION_LIMIT:
                # Текст помещается в caption — отправляем одним сообщением
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            else:
                # Caption не вмещает весь текст — отправляем фото отдельно,
                # затем текст с reply_markup (чтобы кнопки не потерялись).
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file,
                    caption=text[:_TELEGRAM_CAPTION_LIMIT - 3] + "...",
                    parse_mode="HTML",
                )
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            return
        except Exception as e:
            logger.warning("Failed to send photo to %d, falling back to text: %s", user_id, e)

    await bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


@app.post(settings.webhook_path)
async def receive_notification(
    request: Request,
    # FIX: Optional[str] вместо str — если заголовок отсутствует, FastAPI
    # устанавливает None, а не вызывает ошибку валидации 422.
    # С аннотацией str = Header(None) в strict-режиме pydantic v2 / некоторых
    # версиях FastAPI возникает ValidationError вместо корректного 401.
    x_api_token: Optional[str] = Header(None),
):
    """
    Приём уведомлений от API (push-архитектура).

    Маршрут берётся из settings.webhook_path — ранее был захардкожен "/webhook",
    что не соответствовало настройке при изменении переменной окружения.
    """

    # Защита от payload-flooding: проверяем размер тела до парсинга.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_PAYLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Payload too large")
        except ValueError:
            pass  # Некорректный заголовок — дополнительная проверка после чтения тела

    # secrets.compare_digest — timing-safe сравнение токенов.
    # Проверяем до чтения тела: экономим ресурсы при неавторизованных запросах.
    if not x_api_token or not secrets.compare_digest(
        x_api_token.encode(), settings.api_token.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid API token")

    if not bot:
        raise HTTPException(status_code=500, detail="Bot not initialized")

    # Парсим тело вручную (уже прошли size check по заголовку,
    # повторная проверка по реальному размеру защищает от подмены Content-Length)
    try:
        body = await request.body()
        if len(body) > _MAX_PAYLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        payload = NotificationPayload(**json.loads(body))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {e}") from e

    try:
        notification_text = format_notification(payload.model_dump())
        photo_path = payload.meta.get("photo_path") if payload.meta else None

        keyboard = None
        if payload.type == "admin_reservation_request" and payload.book_id:
            keyboard = _make_reservation_keyboard(payload.book_id)

        if payload.user_id == -1:
            # Параллельная рассылка всем администраторам.
            # Один недоступный пользователь не блокирует доставку остальным.
            admin_ids = list(settings.admin_ids_set)
            if admin_ids:
                results = await asyncio.gather(
                    *[
                        _send_to_user(
                            admin_id, notification_text,
                            photo_path=photo_path, reply_markup=keyboard,
                        )
                        for admin_id in admin_ids
                    ],
                    return_exceptions=True,
                )
                for admin_id, result in zip(admin_ids, results):
                    if isinstance(result, Exception):
                        logger.error("Failed to send to admin %d: %s", admin_id, result)

        elif payload.user_id == 0:
            group_id = settings.group_chat_id_int
            if group_id is not None:
                try:
                    await _send_to_user(
                        group_id, notification_text,
                        photo_path=photo_path, reply_markup=keyboard,
                    )
                except Exception as e:
                    logger.error("Failed to send to group %d: %s", group_id, e)
            else:
                logger.debug(
                    "Notification type=%s targeted group (user_id=0) "
                    "but group_chat_id is not configured — skipped",
                    payload.type,
                )

        else:
            try:
                await _send_to_user(
                    payload.user_id, notification_text,
                    photo_path=photo_path, reply_markup=keyboard,
                )
            except Exception as e:
                logger.error("Failed to send to user %d: %s", payload.user_id, e)

        return {"status": "ok"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error processing notification: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": bot is not None}
