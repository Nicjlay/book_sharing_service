"""
Утилиты, разделяемые между модулями приложения.

Вынесены в отдельный файл, чтобы избежать дублирования кода
между main.py и session.py (оба нуждаются в get_env_int до
полной инициализации приложения).
"""
import os


def get_env_int(
    key: str,
    default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """
    Читает целочисленную переменную окружения с проверкой типа и границ.

    При невалидном значении бросает RuntimeError с понятным сообщением,
    а не голый ValueError от int() без контекста.

    Args:
        key:     имя переменной окружения
        default: значение по умолчанию (используется если переменная не задана)
        min_val: нижняя граница (включительно)
        max_val: верхняя граница (включительно)

    Raises:
        RuntimeError: если значение не является целым числом или выходит за границы
    """
    raw = os.getenv(key)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            raise RuntimeError(
                f"Environment variable {key}={raw!r} is not a valid integer. "
                f"Expected an integer, got {type(raw).__name__!r}."
            )
    if min_val is not None and value < min_val:
        raise RuntimeError(
            f"Environment variable {key}={value} is below minimum allowed value {min_val}."
        )
    if max_val is not None and value > max_val:
        raise RuntimeError(
            f"Environment variable {key}={value} exceeds maximum allowed value {max_val}."
        )
    return value
