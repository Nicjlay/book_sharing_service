"""
Валидаторы для проверки пользовательского ввода
"""
import os
from typing import Tuple


def validate_author(author: str) -> Tuple[bool, str]:
    """
    Валидация автора (ТЗ 3.1.1: минимум два слова).
    strip перед split: строка из пробелов «   » не проходит как корректное имя.
    """
    author = author.strip()
    parts = author.split()

    if len(parts) < 2:
        return False, "❌ Пожалуйста, введите полное имя автора (фамилия и имя).\n\nНапример: <b>Достоевский Федор</b>"

    for part in parts:
        if len(part) < 2:
            return False, "❌ Слишком короткое имя. Введите полное имя автора."

    if len(author) > 200:
        return False, "❌ Имя автора слишком длинное (максимум 200 символов)."

    return True, ""


def validate_title(title: str) -> Tuple[bool, str]:
    """Валидация названия книги"""
    title = title.strip()

    if len(title) < 1:
        return False, "❌ Название книги не может быть пустым"

    if len(title) > 200:
        return False, "❌ Название слишком длинное (максимум 200 символов)"

    return True, ""


def validate_description(description: str) -> Tuple[bool, str]:
    """Валидация описания книги"""
    if len(description) > 1000:
        return False, "❌ Описание слишком длинное (максимум 1000 символов)"

    return True, ""


def validate_days(days_str: str) -> Tuple[bool, int, str]:
    """
    Валидация срока бронирования в днях.
    strip перед int(): пробелы не роняют ValueError.
    """
    days_str = (days_str or "").strip()
    try:
        days = int(days_str)
    except ValueError:
        return False, 0, "❌ Пожалуйста, введите число (количество дней)"

    if days < 1:
        return False, 0, "❌ Минимальный срок — 1 день"

    if days > 90:
        return False, 0, "❌ Максимальный срок — 90 дней"

    return True, days, ""


def is_image(file_name: str) -> bool:
    """Проверка расширения файла на изображение"""
    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    _, ext = os.path.splitext(file_name.lower())
    return ext in valid_extensions
