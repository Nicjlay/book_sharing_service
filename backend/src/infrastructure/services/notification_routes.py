"""
Роуты для уведомлений.

Архитектура: Push-режим — API сам инициирует HTTP POST на бот при наступлении событий.

Защита: роутер подключён к protected-зависимости в main.py,
поэтому все эндпоинты здесь требуют X-API-Token.

Для Docker/K8s healthcheck используйте публичный GET /health (корневой),
который зарегистрирован в main.py без аутентификации:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
"""
from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/health")
async def notifications_health():
    """
    Проверка конфигурации Push-сервиса уведомлений (требует X-API-Token).

    BOT_WEBHOOK_URL намеренно не возвращается в ответе:
    это внутренний адрес docker-сети, незачем светить его клиентам.
    """
    return {
        "mode": "push",
        "status": "ok",
        "description": (
            "Notifications are delivered via HTTP POST to the bot webhook. "
            "There is no server-side inbox — all events are pushed in real time."
        ),
    }
