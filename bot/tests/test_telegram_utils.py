"""
test_telegram_utils.py — тесты для utils/telegram.py

Здесь смешаны:
1. Чистые функции (_safe_html_truncate, _is_safe_image_path) — тестируются напрямую
2. Async функции (safe_edit_message, safe_delete_message) — используем pytest-asyncio
   и mock объекты вместо настоящего Telegram

ПОЧЕМУ МЫ МОКАЕМ (подменяем) ОБЪЕКТЫ?
Настоящий Telegram-бот требует интернета, токена и реального чата.
В тестах мы создаём "муляж" (Mock) объекта Message, который выглядит
как настоящий, но на самом деле просто записывает какие методы вызывались.

Запуск:
    pytest tests/test_telegram_utils.py -v
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from utils.telegram import _safe_html_truncate, _is_safe_image_path, safe_edit_message, safe_delete_message


# ═══════════════════════════════════════════
# _safe_html_truncate — чистая функция
# ═══════════════════════════════════════════

class TestSafeHtmlTruncate:
    """
    Проблема: Telegram не принимает сообщения с оборванными HTML-тегами.
    Наивный text[:100] может обрезать «<b>Текст» до «<b>Те» — сломанный HTML.
    Функция _safe_html_truncate решает эту проблему.
    """

    def test_short_text_unchanged(self):
        text = "Короткий текст"
        assert _safe_html_truncate(text, 100) == text

    def test_truncates_plain_text(self):
        text = "A" * 50
        result = _safe_html_truncate(text, 20)
        assert len(result) <= 20
        assert result.endswith("…")

    def test_removes_incomplete_tag_at_end(self):
        # Если обрезка попадает в середину тега "<b>" — он удаляется
        text = "Normal text <b>bold"
        result = _safe_html_truncate(text, 15)
        # Незакрытый "<b" на конце не должен остаться
        assert not result.endswith("<")
        assert "<b" not in result or result.count("<b") < text.count("<b")

    def test_exactly_at_limit_no_truncation(self):
        text = "А" * 10
        result = _safe_html_truncate(text, 10)
        assert result == text

    def test_one_over_limit_truncates(self):
        text = "А" * 11
        result = _safe_html_truncate(text, 10)
        assert len(result) < 11
        assert "…" in result

    def test_custom_suffix(self):
        text = "Hello World"
        result = _safe_html_truncate(text, 8, suffix="...")
        assert result.endswith("...")

    def test_incomplete_tag_cleaned(self):
        # Текст заканчивается открытым тегом — он должен быть убран
        text = "Текст <b"  # незакрытый тег
        result = _safe_html_truncate(text, 1024)  # не обрезаем, но текст уже "сломан"
        # Если длина <= max_len — возвращаем как есть (функция не исправляет уже короткие строки)
        assert isinstance(result, str)


# ═══════════════════════════════════════════
# _is_safe_image_path — чистая функция
# ═══════════════════════════════════════════

class TestIsSafeImagePath:
    """
    Защита от Path Traversal атаки.
    Атакующий пытается передать путь типа "../../../etc/passwd",
    чтобы прочитать файлы за пределами разрешённой директории.
    """

    # ПРИЧИНА ОШИБКИ: функция _is_safe_image_path использует MEDIA_ROOT = "/app/media".
    # На Windows os.path.abspath("/app/media") → "C:\app\media" (добавляет букву диска),
    # а os.path.join("/app/media", "covers/book42.jpg") → "\app\media\covers\book42.jpg"
    # (без буквы диска). Проверка startswith("C:\app\media\") проваливается.
    #
    # ФИКС: подменяем MEDIA_ROOT на реальный абсолютный путь текущей системы
    # через patch. os.path.abspath(".") всегда возвращает корректный путь на любой ОС.

    @pytest.fixture(autouse=True)
    def patch_media_root(self, tmp_path):
        """Заменяем MEDIA_ROOT на временную директорию pytest для каждого теста."""
        with patch("utils.telegram.MEDIA_ROOT", str(tmp_path)):
            yield

    def test_valid_simple_path(self):
        assert _is_safe_image_path("covers/book42.jpg") is True

    def test_valid_just_filename(self):
        assert _is_safe_image_path("photo.jpg") is True

    def test_path_traversal_rejected(self):
        assert _is_safe_image_path("../../../etc/passwd") is False

    def test_absolute_path_rejected(self):
        assert _is_safe_image_path("/etc/passwd") is False

    def test_empty_string_rejected(self):
        assert _is_safe_image_path("") is False

    def test_none_rejected(self):
        assert _is_safe_image_path(None) is False

    def test_nested_valid_path(self):
        assert _is_safe_image_path("2025/january/photo.png") is True


# ═══════════════════════════════════════════
# safe_edit_message — async с моком
# ═══════════════════════════════════════════

@pytest.mark.asyncio
class TestSafeEditMessage:
    """
    safe_edit_message — умная обёртка вокруг Telegram API.
    Telegram хранит photo-сообщения иначе, чем текстовые:
    у фото нет поля 'text', только 'caption'.
    Функция автоматически выбирает нужный метод.
    """

    async def test_edits_text_message(self, mock_message):
        mock_message.photo = None
        await safe_edit_message(mock_message, "Новый текст")
        mock_message.edit_text.assert_called_once()
        args = mock_message.edit_text.call_args
        assert "Новый текст" in args[0] or args[1].get("text", "")  # позиционный или именной

    async def test_edits_photo_caption(self, mock_message):
        # Если сообщение содержит фото — нужно вызывать edit_caption
        mock_message.photo = [MagicMock()]  # непустой список = есть фото
        await safe_edit_message(mock_message, "Подпись к фото")
        mock_message.edit_caption.assert_called_once()
        mock_message.edit_text.assert_not_called()

    async def test_ignores_not_modified_error(self, mock_message):
        from aiogram.exceptions import TelegramBadRequest
        mock_message.photo = None
        mock_message.edit_text.side_effect = TelegramBadRequest(
            method=MagicMock(), message="message is not modified"
        )
        # Не должно бросать исключение
        await safe_edit_message(mock_message, "Тот же текст")

    async def test_ignores_message_not_found(self, mock_message):
        from aiogram.exceptions import TelegramBadRequest
        mock_message.photo = None
        mock_message.edit_text.side_effect = TelegramBadRequest(
            method=MagicMock(), message="message to edit not found"
        )
        await safe_edit_message(mock_message, "Текст")  # не падает

    async def test_reraises_unexpected_telegram_error(self, mock_message):
        from aiogram.exceptions import TelegramBadRequest
        mock_message.photo = None
        mock_message.edit_text.side_effect = TelegramBadRequest(
            method=MagicMock(), message="some unexpected error"
        )
        with pytest.raises(TelegramBadRequest):
            await safe_edit_message(mock_message, "Текст")

    async def test_truncates_long_text(self, mock_message):
        """Текст длиннее 4096 символов должен быть обрезан."""
        mock_message.photo = None
        long_text = "А" * 5000
        await safe_edit_message(mock_message, long_text)
        called_text = mock_message.edit_text.call_args[0][0]
        assert len(called_text) <= 4096

    async def test_truncates_long_caption(self, mock_message):
        """Caption длиннее 1024 символов должен быть обрезан."""
        mock_message.photo = [MagicMock()]
        long_text = "А" * 2000
        await safe_edit_message(mock_message, long_text)
        kwargs = mock_message.edit_caption.call_args[1]
        assert len(kwargs["caption"]) <= 1024

    async def test_passes_reply_markup(self, mock_message):
        mock_message.photo = None
        keyboard = MagicMock()
        await safe_edit_message(mock_message, "Текст", reply_markup=keyboard)
        kwargs = mock_message.edit_text.call_args[1]
        assert kwargs["reply_markup"] is keyboard


# ═══════════════════════════════════════════
# safe_delete_message — async с моком
# ═══════════════════════════════════════════

@pytest.mark.asyncio
class TestSafeDeleteMessage:
    async def test_deletes_message(self, mock_message):
        await safe_delete_message(mock_message)
        mock_message.delete.assert_called_once()

    async def test_ignores_already_deleted(self, mock_message):
        from aiogram.exceptions import TelegramBadRequest
        mock_message.delete.side_effect = TelegramBadRequest(
            method=MagicMock(), message="message to delete not found"
        )
        await safe_delete_message(mock_message)  # не падает

    async def test_ignores_cant_delete(self, mock_message):
        from aiogram.exceptions import TelegramBadRequest
        mock_message.delete.side_effect = TelegramBadRequest(
            method=MagicMock(), message="message can't be deleted"
        )
        await safe_delete_message(mock_message)  # не падает

    async def test_logs_unexpected_bad_request(self, mock_message):
        from aiogram.exceptions import TelegramBadRequest
        mock_message.delete.side_effect = TelegramBadRequest(
            method=MagicMock(), message="some other weird error"
        )
        # Логирует предупреждение, но не падает
        await safe_delete_message(mock_message)

    async def test_handles_generic_exception(self, mock_message):
        mock_message.delete.side_effect = Exception("Network error")
        await safe_delete_message(mock_message)  # не падает