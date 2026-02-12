"""
Webhook endpoint для приема push-уведомлений от API
"""
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Dict
from aiogram import Bot
from config import settings
from utils.formatters import format_notification

app = FastAPI(title="Library Bot Webhook")

# Инициализируем бота (будет установлено в main.py)
bot: Optional[Bot] = None


def set_bot(bot_instance: Bot):
    """Установить экземпляр бота"""
    global bot
    bot = bot_instance


class NotificationPayload(BaseModel):
    """Формат уведомления от API"""
    user_id: int  # -1 для всех админов, 0 для группы
    type: str
    message: str
    book_id: Optional[int] = None
    meta: Dict = {}


@app.post("/webhook")
async def receive_notification(
    payload: NotificationPayload,
    x_api_token: str = Header(None)
):
    """
    Прием уведомлений от API (push-архитектура)
    """
    # Проверка токена
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid API token")
    
    if not bot:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    try:
        # Форматируем уведомление
        notification_text = format_notification(payload.model_dump())
        
        # Определяем получателей
        if payload.user_id == -1:
            # Отправляем всем админам
            for admin_id in settings.admin_ids_list:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=notification_text,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Failed to send to admin {admin_id}: {e}")
        
        elif payload.user_id == 0:
            # Отправляем в группу (если настроена)
            if settings.group_chat_id:
                try:
                    await bot.send_message(
                        chat_id=settings.group_chat_id,
                        text=notification_text,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Failed to send to group: {e}")
        
        else:
            # Отправляем конкретному пользователю
            try:
                await bot.send_message(
                    chat_id=payload.user_id,
                    text=notification_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"Failed to send to user {payload.user_id}: {e}")
        
        return {"status": "ok"}
    
    except Exception as e:
        print(f"Error processing notification: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "bot_initialized": bot is not None
    }
