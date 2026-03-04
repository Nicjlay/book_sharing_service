"""
test_config.py — тесты для config.py (валидация настроек)

Pydantic Settings валидирует все переменные окружения при старте.
Если токен неправильного формата — бот не запустится вообще.
Лучше узнать об этом из теста, чем из сбоя на проде.

ТЕХНИКА: monkeypatch.setenv / os.environ — временно переставляем
переменные окружения только для одного теста.
"""
import os
import pytest

from pydantic import ValidationError


class TestSettings:
    """
    Каждый тест создаёт НОВЫЙ объект Settings с нужными env-переменными.
    Нельзя переиспользовать глобальный settings — он уже создан.
    """

    def _make_settings(self, **overrides):
        """Создаёт Settings с базовыми валидными значениями + overrides."""
        from pydantic_settings import BaseSettings, SettingsConfigDict

        base_env = {
            "BOT_TOKEN": "123456789:AABBCCDDEEFFaabbccddeeff1234567890AB",
            "API_URL": "http://api.example.com",
            "API_TOKEN": "my-secret-token",
        }
        # Временно применяем env-переменные
        old_env = {}
        for key, value in {**base_env, **overrides}.items():
            old_env[key] = os.environ.get(key)
            os.environ[key] = value

        try:
            # Импортируем Settings заново, не используем кешированный singleton
            import importlib
            import config as cfg_module
            importlib.reload(cfg_module)
            return cfg_module.Settings()
        finally:
            # Восстанавливаем env
            for key, old_val in old_env.items():
                if old_val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_val

    # ─────────────────────────────────────
    # bot_token валидация
    # ─────────────────────────────────────

    def test_valid_bot_token(self):
        s = self._make_settings()
        assert s.bot_token.startswith("123456789:")

    def test_invalid_bot_token_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            self._make_settings(BOT_TOKEN="not_a_valid_token")

    def test_empty_bot_token_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            self._make_settings(BOT_TOKEN="")

    def test_bot_token_not_in_repr(self):
        """Токен не должен утекать в repr/str (безопасность логов)."""
        s = self._make_settings()
        assert s.bot_token not in repr(s)

    # ─────────────────────────────────────
    # api_url валидация
    # ─────────────────────────────────────

    def test_api_url_strips_trailing_slash(self):
        s = self._make_settings(API_URL="http://api.example.com/")
        assert not s.api_url.endswith("/")

    def test_api_url_without_scheme_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            self._make_settings(API_URL="api.example.com")

    def test_api_url_https_accepted(self):
        s = self._make_settings(API_URL="https://api.example.com")
        assert s.api_url == "https://api.example.com"

    def test_empty_api_url_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            self._make_settings(API_URL="")

    # ─────────────────────────────────────
    # admin_user_ids парсинг
    # ─────────────────────────────────────

    def test_admin_ids_parsed_correctly(self):
        s = self._make_settings(ADMIN_USER_IDS="111,222,333")
        assert 111 in s.admin_ids_set
        assert 222 in s.admin_ids_set
        assert 333 in s.admin_ids_set

    def test_admin_ids_empty_string(self):
        s = self._make_settings(ADMIN_USER_IDS="")
        assert len(s.admin_ids_set) == 0

    def test_admin_ids_with_spaces(self):
        s = self._make_settings(ADMIN_USER_IDS=" 111 , 222 ")
        assert 111 in s.admin_ids_set
        assert 222 in s.admin_ids_set

    def test_admin_ids_invalid_values_skipped(self):
        s = self._make_settings(ADMIN_USER_IDS="111,abc,222")
        assert 111 in s.admin_ids_set
        assert 222 in s.admin_ids_set
        # "abc" пропускается — не число

    def test_admin_ids_list_property(self):
        s = self._make_settings(ADMIN_USER_IDS="5,10")
        lst = s.admin_ids_list
        assert isinstance(lst, list)
        assert set(lst) == {5, 10}

    # ─────────────────────────────────────
    # group_chat_id парсинг
    # ─────────────────────────────────────

    def test_group_chat_id_negative_int(self):
        s = self._make_settings(GROUP_CHAT_ID="-100123456")
        assert s.group_chat_id_int == -100123456

    def test_group_chat_id_empty_is_none(self):
        s = self._make_settings(GROUP_CHAT_ID="")
        assert s.group_chat_id_int is None

    def test_group_chat_id_invalid_is_none(self):
        s = self._make_settings(GROUP_CHAT_ID="not_a_number")
        assert s.group_chat_id_int is None

    # ─────────────────────────────────────
    # admin_ids_set — O(1) lookup
    # ─────────────────────────────────────

    def test_admin_check_in_set(self):
        s = self._make_settings(ADMIN_USER_IDS="42")
        assert 42 in s.admin_ids_set
        assert 99 not in s.admin_ids_set
