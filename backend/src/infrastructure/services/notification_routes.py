"""
Дополнительные эндпоинты для уведомлений.
Добавьте эти роуты в main.py или подключите как отдельный роутер.
"""
from fastapi import APIRouter

from infrastructure.db.session import get_db
from infrastructure.services.background_tasks import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/user/{user_id}")
async def get_user_notifications(user_id: int):
    """
    Получить все уведомления пользователя.
    В реальном приложении это будет работать через WebSocket или Server-Sent Events.
    """
    notifications = notification_service.get_notifications(user_id)
    return {
        "user_id": user_id,
        "count": len(notifications),
        "notifications": notifications
    }


@router.delete("/user/{user_id}")
async def clear_user_notifications(user_id: int):
    """Очистить все уведомления пользователя."""
    notification_service.clear_notifications(user_id)
    return {
        "status": "ok",
        "message": "Уведомления очищены"
    }


@router.get("/")
async def get_all_notifications():
    """
    Получить все уведомления (для администраторов).
    В продакшене нужна авторизация!
    """
    return {
        "count": len(notification_service.notifications_queue),
        "notifications": notification_service.notifications_queue
    }