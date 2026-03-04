"""
test_validators.py — тесты для utils/validators.py

Валидаторы — тоже чистые функции, 100% покрытие достижимо.
Каждый тест проверяет один конкретный граничный случай (boundary condition).

Граничные случаи — это самое важное в тестировании:
- Минимально допустимое значение
- Максимально допустимое значение
- Значение за границей (один шаг выше/ниже лимита)
- Пустая строка / None
- Пробелы
"""
import pytest
from utils.validators import is_image, validate_author, validate_days, validate_description, validate_title


# ═══════════════════════════════════════════
# validate_author
# ═══════════════════════════════════════════

class TestValidateAuthor:
    def test_valid_two_words(self):
        ok, msg = validate_author("Достоевский Федор")
        assert ok is True
        assert msg == ""

    def test_valid_three_words(self):
        ok, msg = validate_author("Лев Николаевич Толстой")
        assert ok is True

    def test_single_word_fails(self):
        ok, msg = validate_author("Пушкин")
        assert ok is False
        assert "полное имя" in msg.lower() or "фамилия" in msg.lower()

    def test_empty_string_fails(self):
        ok, msg = validate_author("")
        assert ok is False

    def test_only_spaces_fails(self):
        ok, msg = validate_author("   ")
        assert ok is False

    def test_short_word_part_fails(self):
        # Каждая часть должна быть >= 2 символов
        ok, msg = validate_author("А Иванов")
        assert ok is False
        assert "короткое" in msg.lower()

    def test_too_long_fails(self):
        long_name = "А" * 100 + " " + "Б" * 101  # > 200 символов
        ok, msg = validate_author(long_name)
        assert ok is False
        assert "длинное" in msg.lower() or "200" in msg

    def test_exactly_200_chars_passes(self):
        # "АА" + пробел + "А" * 197 = 200 символов
        name = "АА " + "А" * 197
        ok, _ = validate_author(name)
        assert ok is True

    def test_strips_leading_trailing_spaces(self):
        ok, _ = validate_author("  Иванов Иван  ")
        assert ok is True

    def test_two_letter_parts_valid(self):
        ok, _ = validate_author("Ли Ма")
        assert ok is True


# ═══════════════════════════════════════════
# validate_title
# ═══════════════════════════════════════════

class TestValidateTitle:
    def test_valid_title(self):
        ok, msg = validate_title("Война и мир")
        assert ok is True
        assert msg == ""

    def test_empty_string_fails(self):
        ok, msg = validate_title("")
        assert ok is False
        assert "пустым" in msg.lower()

    def test_only_spaces_fails(self):
        ok, msg = validate_title("   ")
        assert ok is False

    def test_single_char_passes(self):
        ok, _ = validate_title("А")
        assert ok is True

    def test_exactly_200_chars_passes(self):
        ok, _ = validate_title("А" * 200)
        assert ok is True

    def test_201_chars_fails(self):
        ok, msg = validate_title("А" * 201)
        assert ok is False
        assert "200" in msg


# ═══════════════════════════════════════════
# validate_description
# ═══════════════════════════════════════════

class TestValidateDescription:
    def test_empty_passes(self):
        ok, _ = validate_description("")
        assert ok is True

    def test_normal_description(self):
        ok, _ = validate_description("Отличная книга о жизни.")
        assert ok is True

    def test_exactly_1000_chars_passes(self):
        ok, _ = validate_description("А" * 1000)
        assert ok is True

    def test_1001_chars_fails(self):
        ok, msg = validate_description("А" * 1001)
        assert ok is False
        assert "1000" in msg


# ═══════════════════════════════════════════
# validate_days
# ═══════════════════════════════════════════

class TestValidateDays:
    def test_valid_7_days(self):
        ok, days, msg = validate_days("7")
        assert ok is True
        assert days == 7
        assert msg == ""

    def test_valid_1_day_minimum(self):
        ok, days, _ = validate_days("1")
        assert ok is True
        assert days == 1

    def test_valid_90_days_maximum(self):
        ok, days, _ = validate_days("90")
        assert ok is True
        assert days == 90

    def test_zero_fails(self):
        ok, _, msg = validate_days("0")
        assert ok is False
        assert "1" in msg

    def test_negative_fails(self):
        ok, _, msg = validate_days("-5")
        assert ok is False

    def test_91_days_fails(self):
        ok, _, msg = validate_days("91")
        assert ok is False
        assert "90" in msg

    def test_non_numeric_fails(self):
        ok, _, msg = validate_days("abc")
        assert ok is False
        assert "число" in msg.lower()

    def test_float_fails(self):
        ok, _, _ = validate_days("7.5")
        assert ok is False

    def test_spaces_stripped(self):
        ok, days, _ = validate_days("  14  ")
        assert ok is True
        assert days == 14

    def test_empty_string_fails(self):
        ok, _, msg = validate_days("")
        assert ok is False

    def test_none_fails(self):
        ok, _, msg = validate_days(None)
        assert ok is False


# ═══════════════════════════════════════════
# is_image
# ═══════════════════════════════════════════

class TestIsImage:
    @pytest.mark.parametrize("filename", [
        "photo.jpg",
        "photo.jpeg",
        "photo.png",
        "photo.webp",
        "PHOTO.JPG",          # верхний регистр
        "my.photo.jpg",       # несколько точек
    ])
    def test_valid_image_extensions(self, filename):
        assert is_image(filename) is True

    @pytest.mark.parametrize("filename", [
        "document.pdf",
        "archive.zip",
        "script.exe",
        "data.csv",
        "noextension",
        "photo.gif",     # gif не поддерживается
        "photo.bmp",
    ])
    def test_invalid_extensions(self, filename):
        assert is_image(filename) is False
