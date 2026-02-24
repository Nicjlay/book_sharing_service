"""
Webhook endpoint для приема push-уведомлений от API
"""
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Dict
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import settings
from utils.formatters import format_notification
import httpx

app = FastAPI(title="Library Bot Webhook")

bot: Optional[Bot] = None


def set_bot(bot_instance: Bot):
    """Установить экземпляр бота"""
    global bot
    bot = bot_instance


class NotificationPayload(BaseModel):
    """Формат уведомления от API"""
    user_id: int        # -1 = всем админам, 0 = в группу
    type: str
    message: str
    book_id: Optional[int] = None
    meta: Dict = {}


# Singleton HTTP client — один SSL context на весь процесс
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=10.0)
    return _http_client


async def _fetch_photo(photo_path: str) -> Optional[bytes]:
    """Скачивает фото с API по внутреннему пути"""
    if not photo_path:
        return None
    try:
        api_url = settings.api_url.rstrip("/")
        url = f"{api_url}/media/{photo_path}"
        client = _get_http_client()
        resp = await client.get(url)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        print(f"⚠️ Failed to fetch photo {photo_path}: {e}")
    return None


def _make_reservation_keyboard(book_id: int) -> InlineKeyboardMarkup:
    """Кнопки быстрого действия для уведомления о заявке на бронирование"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Выдать книгу", callback_data=f"approve:{book_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{book_id}")
    )
    return builder.as_markup()


async def _send_to_user(user_id: int, text: str, photo_bytes: Optional[bytes] = None,
                         photo_path: str = None, reply_markup=None):
    """Отправляет уведомление пользователю. Если есть фото — шлёт отдельным сообщением."""
    await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML",
                           reply_markup=reply_markup)

    # Фото — отдельным сообщением после текста
    if not photo_bytes and photo_path:
        photo_bytes = await _fetch_photo(photo_path)

    if photo_bytes:
        try:
            photo_file = BufferedInputFile(photo_bytes, filename="photo.jpg")
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_file,
                caption="📸 Фото книги при возврате"
            )
        except Exception as e:
            print(f"⚠️ Failed to send photo to {user_id}: {e}")


@app.post("/webhook")
async def receive_notification(
    payload: NotificationPayload,
    x_api_token: str = Header(None)
):
    """Прием уведомлений от API (push-архитектура)"""
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid API token")

    if not bot:
        raise HTTPException(status_code=500, detail="Bot not initialized")

    try:
        notification_text = format_notification(payload.model_dump())

        # Фото из meta (только для book_returned)
        photo_path = payload.meta.get("photo_path") if payload.meta else None

        # Кнопки быстрого действия для заявок на бронирование
        keyboard = None
        if payload.type == "admin_reservation_request" and payload.book_id:
            keyboard = _make_reservation_keyboard(payload.book_id)

        if payload.user_id == -1:
            # Всем админам
            for admin_id in settings.admin_ids_list:
                try:
                    await _send_to_user(admin_id, notification_text, photo_path=photo_path,
                                        reply_markup=keyboard)
                except Exception as e:
                    print(f"Failed to send to admin {admin_id}: {e}")

        elif payload.user_id == 0:
            # В группу
            if settings.group_chat_id:
                try:
                    await _send_to_user(settings.group_chat_id, notification_text,
                                        photo_path=photo_path, reply_markup=keyboard)
                except Exception as e:
                    print(f"Failed to send to group: {e}")

        else:
            # Конкретному пользователю
            try:
                await _send_to_user(payload.user_id, notification_text, photo_path=photo_path,
                                    reply_markup=keyboard)
            except Exception as e:
                print(f"Failed to send to user {payload.user_id}: {e}")

        return {"status": "ok"}

    except Exception as e:
        print(f"Error processing notification: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": bot is not None}


@app.on_event("shutdown")
async def shutdown():
    """Закрываем HTTP клиент при остановке — освобождаем SSL context."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        print("🛑 Webhook HTTP client closed")