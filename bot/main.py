"""
Главный файл Telegram бота для библиотеки
"""
import asyncio
import logging
import signal
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import webhook as webhook_module
from api.client import api
from config import settings
from handlers import admin, catalog, my_books, reservation, search, start, wizard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота"""

    # ВНИМАНИЕ: MemoryStorage хранит FSM-состояния только в памяти процесса.
    # При перезапуске контейнера (docker restart / OOM kill) все активные
    # диалоги пользователей будут сброшены. Для production-окружения
    # замените на RedisStorage:
    #   from aiogram.fsm.storage.redis import RedisStorage
    #   storage = RedisStorage.from_url(settings.redis_url)
    storage = MemoryStorage()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)

    # Регистрация handlers (порядок важен для приоритета фильтров)
    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(search.router)   # после catalog — фильтры не конфликтуют
    dp.include_router(wizard.router)
    dp.include_router(reservation.router)
    dp.include_router(admin.router)
    dp.include_router(my_books.router)

    webhook_module.set_bot(bot)

    config = uvicorn.Config(
        webhook_module.app,
        host=settings.webhook_host,
        port=settings.webhook_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    # Сохраняем ссылку на task: без этого GC может уничтожить задачу
    # при нехватке памяти (CPython >= 3.12 выдаёт предупреждение).
    webhook_task = asyncio.create_task(server.serve())

    logger.info("🚀 Bot started!")
    logger.info("📡 Webhook listening on %s:%d%s",
                settings.webhook_host, settings.webhook_port, settings.webhook_path)
    logger.info("🔗 API endpoint: %s", settings.api_url)
    logger.info("👨‍💼 Admins count: %d", len(settings.admin_ids_set))

    loop = asyncio.get_running_loop()

    def _handle_sigterm():
        """
        Graceful shutdown при SIGTERM (docker stop).

        Порядок важен:
        1. Останавливаем polling aiogram — перестаём принимать updates.
        2. Сигнализируем uvicorn завершиться — `should_exit = True` изнутри
           event loop. Это инициирует корректное завершение HTTP-сервера:
           дожидается активных запросов и закрывает соединения.
        """
        logger.info("SIGTERM received — initiating graceful shutdown...")
        asyncio.create_task(dp.stop_polling())
        server.should_exit = True

    loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)

    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        try:
            loop.remove_signal_handler(signal.SIGTERM)
        except Exception:
            pass

        # Даём uvicorn корректно завершиться (if not already)
        server.should_exit = True
        try:
            await asyncio.wait_for(webhook_task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Uvicorn did not stop in time, cancelling task")
            webhook_task.cancel()
            try:
                await webhook_task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

        await bot.session.close()
        await api.close()
        logger.info("🛑 All resources closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error("Fatal error: %s", e)
        sys.exit(1)
