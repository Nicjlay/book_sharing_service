"""
Роуты для уведомлений.

Архитектурная заметка:
    Сервис работает в Push-режиме: API сам инициирует HTTP POST на бот
    при наступлении событий (новая книга, возврат, просрочка и т.д.).
    Никакого хранилища уведомлений на стороне API нет и не предполагается.

    Исходный файл содержал три эндпоинта, вызывавших методы
    get_notifications / clear_notifications / notifications_queue,
    которые никогда не существовали в NotificationService. Все три
    роута падали с AttributeError при первом обращении.

    Fix #2: нерабочие эндпоинты удалены. Если в будущем понадобится
    inbox-модель (пользователь «подтягивает» уведомления), нужно добавить
    отдельную таблицу notification_log и реализовать хранение там.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/health")
async def notifications_health():
    """
    Проверка доступности Push-сервиса уведомлений.
    Возвращает текущий режим работы для диагностики.
    """
    import os
    bot_url = os.getenv("BOT_WEBHOOK_URL", "http://library_bot:8001/webhook")
    return {
        "mode": "push",
        "bot_webhook_url": bot_url,
        "description": (
            "Notifications are delivered via HTTP POST to the bot webhook. "
            "There is no server-side inbox — all events are pushed in real time."
        ),
    }