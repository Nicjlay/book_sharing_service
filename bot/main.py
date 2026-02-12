"""
Главный файл Telegram бота для библиотеки
"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
import uvicorn
from threading import Thread

from config import settings
import webhook as webhook_module

# Импортируем все handlers
from handlers import (
    start,
    catalog,
    wizard,
    reservation,
    admin,
    my_books
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота"""
    
    # Инициализация бота и диспетчера
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрация handlers
    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(wizard.router)
    dp.include_router(reservation.router)
    dp.include_router(admin.router)
    dp.include_router(my_books.router)
    
    # Устанавливаем бота в webhook модуль
    webhook_module.set_bot(bot)
    
    # Запускаем webhook сервер в отдельном потоке
    def run_webhook():
        uvicorn.run(
            webhook_module.app,
            host=settings.webhook_host,
            port=settings.webhook_port,
            log_level="info"
        )
    
    webhook_thread = Thread(target=run_webhook, daemon=True)
    webhook_thread.start()
    
    logger.info("🚀 Bot started!")
    logger.info(f"📡 Webhook listening on {settings.webhook_host}:{settings.webhook_port}")
    logger.info(f"🔗 API endpoint: {settings.api_url}")
    logger.info(f"👨‍💼 Admins: {settings.admin_ids_list}")
    
    # Удаляем старый webhook (если был)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
