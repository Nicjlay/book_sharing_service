"""
Валидаторы для проверки пользовательского ввода
"""
from typing import Tuple


def validate_author(author: str) -> Tuple[bool, str]:
    """
    Валидация автора (ТЗ 3.1.1: должно быть минимум два слова)
    
    Returns:
        (is_valid, error_message)
    """
    parts = author.strip().split()
    
    if len(parts) < 2:
        return False, "❌ Пожалуйста, введите полное имя автора (фамилия и имя).\n\nНапример: <b>Достоевский Федор</b>"
    
    # Проверка на минимальную длину каждого слова
    for part in parts:
        if len(part) < 2:
            return False, "❌ Слишком короткое имя. Введите полное имя автора."
    
    return True, ""


def validate_title(title: str) -> Tuple[bool, str]:
    """
    Валидация названия книги
    
    Returns:
        (is_valid, error_message)
    """
    title = title.strip()
    
    if len(title) < 1:
        return False, "❌ Название книги не может быть пустым"
    
    if len(title) > 200:
        return False, "❌ Название слишком длинное (максимум 200 символов)"
    
    return True, ""


def validate_description(description: str) -> Tuple[bool, str]:
    """
    Валидация описания книги
    
    Returns:
        (is_valid, error_message)
    """
    if len(description) > 1000:
        return False, "❌ Описание слишком длинное (максимум 1000 символов)"
    
    return True, ""


def validate_days(days_str: str) -> Tuple[bool, int, str]:
    """
    Валидация срока бронирования в днях
    
    Returns:
        (is_valid, days, error_message)
    """
    try:
        days = int(days_str)
    except ValueError:
        return False, 0, "❌ Пожалуйста, введите число (количество дней)"
    
    if days < 1:
        return False, 0, "❌ Минимальный срок - 1 день"
    
    if days > 90:
        return False, 0, "❌ Максимальный срок - 90 дней"
    
    return True, days, ""


def is_image(file_name: str) -> bool:
    """
    Проверка, является ли файл изображением
    """
    valid_extensions = ['.jpg', '.jpeg', '.png', '.webp']
    return any(file_name.lower().endswith(ext) for ext in valid_extensions)
