"""
Конфигурация Telegram бота
"""
from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Настройки бота из переменных окружения"""
    
    # Telegram Bot
    bot_token: str
    
    # API Backend
    api_url: str = "http://localhost:8000"
    api_token: str
    
    # Admin Users (Telegram IDs)
    admin_user_ids: str = "964229335"
    
    # Webhook для приема уведомлений от API
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8001
    webhook_path: str = "/webhook"
    
    # Group Chat ID для уведомлений о новых книгах
    group_chat_id: str = ""  # Опционально
    
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
