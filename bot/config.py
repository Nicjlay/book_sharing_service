"""
Конфигурация Telegram бота
"""
import logging
import re
from typing import FrozenSet, List, Optional

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Telegram bot token format: <digits>:<alphanumeric+_->
_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{35,}$")

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Настройки бота из переменных окружения / .env файла.

    Используем SettingsConfigDict вместо устаревшего inner-class Config:
    Pydantic v2 deprecates `class Config` в пользу `model_config`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # extra="ignore": неизвестные ключи в .env молча пропускаются.
        # Это безопаснее для production — бот не падает при добавлении новых
        # переменных или при опечатках в несвязанных полях.
        # Если нужно ловить опечатки в ключах — замените на extra="forbid".
        extra="ignore",
    )

    # Telegram Bot
    # repr=False исключает значение из __repr__ и __str__ объекта Settings,
    # предотвращая случайное попадание токенов в логи при логировании settings.
    bot_token: str = Field(repr=False)

    # API Backend
    api_url: str
    api_token: str = Field(repr=False)

    # Admin Users (Telegram IDs через запятую: "123,456,789")
    admin_user_ids: str = ""

    # Webhook для приема уведомлений от API
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_path: str = "/webhook"

    # Group Chat ID для уведомлений о новых книгах (опционально).
    # Принимаем строку, потому что ID группы может начинаться с «-»
    # и pydantic-settings некоторых версий падает при парсинге отрицательного int.
    group_chat_id: str = ""

    # Приватные атрибуты — не попадают в env/JSON, вычисляются в model_validator
    _admin_ids: FrozenSet[int] = PrivateAttr(default_factory=frozenset)
    _group_chat_id_int: Optional[int] = PrivateAttr(default=None)

    @field_validator("bot_token")
    @classmethod
    def _validate_bot_token(cls, v: str) -> str:
        if not v:
            raise ValueError("bot_token не может быть пустым")
        if not _BOT_TOKEN_RE.match(v):
            raise ValueError(
                "Некорректный формат bot_token. "
                "Ожидается формат: <числа>:<35+ символов a-zA-Z0-9_->. "
                "Получите токен у @BotFather."
            )
        return v

    @field_validator("api_url")
    @classmethod
    def _validate_api_url(cls, v: str) -> str:
        if not v:
            raise ValueError("api_url не может быть пустым")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("api_url должен начинаться с http:// или https://")
        return v.rstrip("/")

    @field_validator("api_token")
    @classmethod
    def _validate_api_token(cls, v: str) -> str:
        if not v:
            raise ValueError("api_token не может быть пустым")
        return v

    @model_validator(mode="after")
    def _build_derived(self) -> "Settings":
        """Парсим admin_user_ids и group_chat_id один раз при старте."""

        # --- admin ids ---
        # Telegram user IDs — всегда положительные целые числа.
        # Отрицательные ID — это чаты/каналы, не пользователи → фильтруем.
        ids: FrozenSet[int] = frozenset()
        if self.admin_user_ids:
            parsed = []
            for part in self.admin_user_ids.split(","):
                part = part.strip()
                if part.isdigit():
                    uid = int(part)
                    if uid > 0:
                        parsed.append(uid)
                    else:
                        logger.warning("Skipping non-positive admin_id: %d", uid)
            ids = frozenset(parsed)
        object.__setattr__(self, "_admin_ids", ids)

        # --- group chat id ---
        gid: Optional[int] = None
        raw = self.group_chat_id.strip()
        if raw:
            try:
                gid = int(raw)
            except ValueError:
                logger.warning(
                    "group_chat_id=%r is not a valid integer — group notifications disabled",
                    raw,
                )
        object.__setattr__(self, "_group_chat_id_int", gid)

        return self

    @property
    def admin_ids_set(self) -> FrozenSet[int]:
        """O(1) проверка принадлежности: `user_id in settings.admin_ids_set`."""
        return self._admin_ids

    @property
    def admin_ids_list(self) -> List[int]:
        """Для обратной совместимости с кодом, который ожидает list."""
        return list(self._admin_ids)

    @property
    def group_chat_id_int(self) -> Optional[int]:
        """Валидированный int или None если не задан / невалиден."""
        return self._group_chat_id_int


settings = Settings()
