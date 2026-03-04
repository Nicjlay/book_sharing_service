"""
Тесты utils.get_env_int — единственной публичной функции модуля.

Юнит-тест: тестируется изолированная функция без зависимостей.
Патчим os.getenv чтобы не зависеть от реального окружения.
"""
import pytest
from unittest.mock import patch
from utils import get_env_int


class TestGetEnvInt:
    """Группируем тесты одной функции в класс для наглядности."""

    # ── Базовое поведение ──────────────────────────────────────────────────

    def test_returns_default_when_var_not_set(self):
        """Если переменная не задана — возвращается default."""
        with patch.dict("os.environ", {}, clear=True):
            assert get_env_int("MISSING_VAR", default=42) == 42

    def test_returns_parsed_value_when_var_is_set(self):
        """Если переменная задана корректно — возвращается int."""
        with patch.dict("os.environ", {"MY_INT": "100"}):
            assert get_env_int("MY_INT", default=0) == 100

    def test_returns_zero(self):
        """Ноль — корректное целое число."""
        with patch.dict("os.environ", {"MY_INT": "0"}):
            assert get_env_int("MY_INT", default=5) == 0

    def test_returns_negative(self):
        """Отрицательные числа разрешены (если нет min_val)."""
        with patch.dict("os.environ", {"MY_INT": "-10"}):
            assert get_env_int("MY_INT", default=0) == -10

    # ── Ошибки типа ───────────────────────────────────────────────────────

    def test_raises_on_non_integer_string(self):
        """Строка не являющаяся числом → RuntimeError с понятным сообщением."""
        with patch.dict("os.environ", {"MY_INT": "abc"}):
            with pytest.raises(RuntimeError, match="not a valid integer"):
                get_env_int("MY_INT", default=0)

    def test_raises_on_float_string(self):
        """Дробное число — тоже не целое."""
        with patch.dict("os.environ", {"MY_INT": "3.14"}):
            with pytest.raises(RuntimeError, match="not a valid integer"):
                get_env_int("MY_INT", default=0)

    def test_raises_on_empty_string(self):
        """Пустая строка — невалидный int."""
        with patch.dict("os.environ", {"MY_INT": ""}):
            with pytest.raises(RuntimeError, match="not a valid integer"):
                get_env_int("MY_INT", default=0)

    # ── Граничные условия ─────────────────────────────────────────────────

    def test_raises_when_below_min_val(self):
        """Значение меньше min_val → RuntimeError."""
        with patch.dict("os.environ", {"MY_INT": "0"}):
            with pytest.raises(RuntimeError, match="below minimum"):
                get_env_int("MY_INT", default=5, min_val=1)

    def test_passes_when_equal_to_min_val(self):
        """Значение равно min_val → разрешено (граница включительно)."""
        with patch.dict("os.environ", {"MY_INT": "1"}):
            assert get_env_int("MY_INT", default=0, min_val=1) == 1

    def test_raises_when_above_max_val(self):
        """Значение больше max_val → RuntimeError."""
        with patch.dict("os.environ", {"MY_INT": "100"}):
            with pytest.raises(RuntimeError, match="exceeds maximum"):
                get_env_int("MY_INT", default=5, max_val=99)

    def test_passes_when_equal_to_max_val(self):
        """Значение равно max_val → разрешено (граница включительно)."""
        with patch.dict("os.environ", {"MY_INT": "99"}):
            assert get_env_int("MY_INT", default=0, max_val=99) == 99

    def test_default_also_checked_against_min_val(self):
        """Даже дефолтное значение проверяется на границы."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="below minimum"):
                get_env_int("MISSING", default=0, min_val=1)

    def test_default_also_checked_against_max_val(self):
        """Даже дефолтное значение проверяется на границы."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="exceeds maximum"):
                get_env_int("MISSING", default=100, max_val=50)

    def test_no_bounds_check_when_none(self):
        """Без min_val/max_val любое число проходит."""
        with patch.dict("os.environ", {"MY_INT": "-9999"}):
            assert get_env_int("MY_INT", default=0) == -9999

    def test_error_message_contains_key_and_value(self):
        """Сообщение об ошибке должно содержать имя переменной и значение."""
        with patch.dict("os.environ", {"POOL_SIZE": "bad"}):
            with pytest.raises(RuntimeError) as exc_info:
                get_env_int("POOL_SIZE", default=0)
            assert "POOL_SIZE" in str(exc_info.value)
            assert "bad" in str(exc_info.value)