from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    """
    Централизованная конфигурация приложения.
    Загружает переменные из .env файла.
    """

    # Database
    database_url: str = "sqlite+aiosqlite:///./library.db"

    # Security
    api_token: str = "change-me-in-production"

    # Bot Integration
    bot_webhook_url: str = "http://localhost:8001/webhook"

    # Admin Users
    admin_user_ids: str = ""  # Comma-separated Telegram IDs

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    # Background Tasks
    check_overdue_interval: int = 3600  # seconds
    due_date_reminder_days: int = 3

    @property
    def admin_ids_list(self) -> List[int]:
        """Получить список админов как list[int]"""
        if not self.admin_user_ids:
            return []
        return [int(id.strip()) for id in self.admin_user_ids.split(",") if id.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Singleton instance
settings = Settings()