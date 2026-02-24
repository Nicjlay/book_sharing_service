"""
Роуты для уведомлений.

Архитектура: Push-режим — API сам инициирует HTTP POST на бот при наступлении событий.
Никакого inbox/очереди на стороне API нет.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/health")
async def notifications_health():
    """Проверка конфигурации Push-сервиса уведомлений."""
    import os
    bot_url = os.getenv("BOT_WEBHOOK_URL", "http://library_bot:8001/webhook")
    return {
        "mode":             "push",
        "bot_webhook_url":  bot_url,
        "description": (
            "Notifications are delivered via HTTP POST to the bot webhook. "
            "There is no server-side inbox — all events are pushed in real time."
        ),
    }