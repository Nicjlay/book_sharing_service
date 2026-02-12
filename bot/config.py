"""
Конфигурация Telegram бота
"""
from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Настройки бота из переменных окружения"""
    
    # Telegram Bot
    bot_token: str = os.getenv("BOT_TOKEN")
    
    # API Backend
    api_url: str = os.getenv("API_URL")
    api_token: str = os.getenv("API_TOKEN")
    
    # Admin Users (Telegram IDs)
    admin_user_ids: str = os.getenv("ADMIN_USER_IDS")
    
    # Webhook для приема уведомлений от API
    webhook_host: str = os.getenv("WEBHOOK_HOST")
    webhook_port: int = os.getenv("WEBHOOK_PORT")
    webhook_path: str = os.getenv("WEBHOOK_PATH")
    
    # Group Chat ID для уведомлений о новых книгах
    group_chat_id: str = os.getenv("GROUP_CHAT_ID")
    
    @property
    def admin_ids_list(self) -> List[int]:
        """Получить список админов как list[int]"""
        if not self.admin_user_ids:
            return []
        return [int(id.strip()) for id in self.admin_user_ids.split(",") if id.strip()]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
