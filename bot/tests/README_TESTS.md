
# 🧪 Руководство по тестированию для новичков

## Часть 1: Как работает ваш бот

### Общая архитектура

```
Пользователь в Telegram
        ↓
   [aiogram Bot]          ← принимает сообщения/нажатия кнопок
        ↓
   [Handlers]             ← обрабатывают события (start.py, catalog.py, ...)
        ↓
   [API Client]           ← делает HTTP-запросы к вашему бэкенду
        ↓
   [Library Backend]      ← хранит книги в базе данных
```

Параллельно работает **Webhook-сервер** (FastAPI):
```
Бэкенд → POST /webhook → webhook.py → Bot.send_message() → Пользователь
```
Это нужно чтобы бэкенд мог **сам отправлять уведомления** пользователям
(например: «Ваша книга одобрена!»).

---

### Файлы и их назначение

| Файл | Что делает |
|------|-----------|
| `config.py` | Читает токены и настройки из `.env` файла |
| `main.py` | Точка входа. Запускает бота и webhook-сервер |
| `api/client.py` | HTTP-клиент. Делает запросы к вашему API |
| `utils/formatters.py` | Форматирует данные в красивые Telegram-сообщения |
| `utils/validators.py` | Проверяет корректность ввода пользователя |
| `utils/telegram.py` | Вспомогательные функции для работы с Telegram |
| `keyboards/inline.py` | Создаёт кнопки под сообщениями |
| `states/wizard.py` | Описывает шаги пошаговых диалогов (FSM) |
| `handlers/*.py` | Обрабатывают команды и нажатия кнопок |
| `webhook.py` | Принимает push-уведомления от API |

---

### Что такое FSM (конечный автомат)?

Когда пользователь добавляет книгу через `/add`, идёт пошаговый диалог:

```
Шаг 1: Введите автора
   ↓
Шаг 2: Введите название
   ↓
Шаг 3: Загрузите фото (или пропустите)
   ↓
Шаг 4: Введите описание
   ↓
Шаг 5: Выберите владельца
   ↓
Шаг 6: Подтвердите
```

FSM (Finite State Machine) «запоминает», на каком шаге находится пользователь.
Это реализовано в `states/wizard.py` через `StatesGroup`.

---

## Часть 2: Что такое тесты и зачем они нужны

### Аналогия

Представьте, что вы строите мост. Перед сдачей вы:
- Проверяете каждый болт (unit tests)
- Проверяете секцию целиком (integration tests)
- Прогоняете по мосту грузовик (end-to-end tests)

В программировании:
- **Unit тест** — проверяет одну функцию в изоляции
- **Integration тест** — проверяет несколько компонентов вместе
- **E2E тест** — имитирует реального пользователя

В этом проекте мы пишем в основном **unit тесты**.

---

### Почему не 100% покрытие?

**100% coverage теоретически возможно, но практически бессмысленно для части кода.**

Вот что сложно/невозможно покрыть:

#### 1. `main.py` — точка входа
```python
async def main():
    storage = MemoryStorage()
    bot = Bot(token=settings.bot_token, ...)
    # Запускает uvicorn + aiogram polling
    await dp.start_polling(bot, ...)
```
Это нельзя нормально тестировать в unit-тестах:
- Создаёт реальный сервер
- Требует реального токена бота
- Бесконечный цикл polling
- Обработчик SIGTERM

**Решение**: тестируют через интеграционные тесты или не тестируют вообще.
Такой код должен быть максимально простым — только «склейка» компонентов.

#### 2. Обработчики (handlers/*.py) — сложная зависимость от aiogram

```python
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await api.auth_user(...)
    await message.answer(...)
```

Тут нужно мокировать:
- `message` — объект с десятками полей
- `state` — FSM контекст
- `api` — HTTP клиент
- Aiogram внутренности

Это делается, но требует много кода. Такие тесты называются **интеграционными**.

#### 3. Retry-логика с реальными задержками

```python
# В client.py
await asyncio.sleep(wait)  # wait = 5 секунд
```

Тестировать это без `asyncio.sleep = MagicMock()` займёт несколько минут.
С mock — потеряется смысл теста (проверяем что sleep вызвался, а не что retry работает).

#### 4. Что реально покрывается нашими тестами:

| Модуль | Покрытие | Комментарий |
|--------|----------|-------------|
| `utils/formatters.py` | ~95-100% | Чистые функции |
| `utils/validators.py` | ~100% | Чистые функции |
| `utils/telegram.py` | ~85% | Async с моками |
| `keyboards/inline.py` | ~90% | Условная логика кнопок |
| `webhook.py` | ~80% | FastAPI TestClient |
| `api/client.py` | ~70% | Часть retry-логики сложна |
| `config.py` | ~75% | Основные случаи |
| `handlers/*.py` | ~30% | Сложная зависимость от aiogram |
| `main.py` | ~5% | Точка входа |

---

## Часть 3: Структура тестов

### Как выглядит тест

```python
# test_validators.py

def test_single_word_fails():          # ← имя теста описывает ЧТО тестируем
    ok, msg = validate_author("Пушкин")  # ← вызываем функцию
    assert ok is False                   # ← проверяем результат
    assert "полное имя" in msg.lower()   # ← проверяем сообщение об ошибке
```

Принцип **AAA** (Arrange, Act, Assert):
```python
def test_book_card_shows_author():
    # ARRANGE — подготавливаем данные
    book = {"id": 1, "title": "Война и мир", "author": "Толстой Лев", "status": "available"}
    
    # ACT — вызываем тестируемый код
    text = format_book_card(book)
    
    # ASSERT — проверяем результат
    assert "Толстой Лев" in text
```

### Как работают Мок-объекты

```python
from unittest.mock import AsyncMock, MagicMock

# MagicMock — создаёт объект который притворяется чем угодно
message = MagicMock()
message.photo = None           # задаём нужные значения
message.edit_text = AsyncMock() # AsyncMock для async методов

# После вызова тестируемого кода — проверяем что мок вызвался
await safe_edit_message(message, "Текст")
message.edit_text.assert_called_once()  # был вызван ровно один раз
```

Зачем это нужно: настоящий `Message` требует подключения к Telegram.
MagicMock — это "кукла", которая выглядит как `Message`, но ничего не отправляет.

### Параметризованные тесты

```python
@pytest.mark.parametrize("notif_type,expected_emoji", [
    ("reservation_approved", "✅"),
    ("overdue", "🚨"),
    ("new_book", "📖"),
])
def test_emoji_by_type(notif_type, expected_emoji):
    notif = {"type": notif_type, "message": "Текст"}
    text = format_notification(notif)
    assert expected_emoji in text
```

Один тест запускается **три раза** с разными данными. Удобно для проверки
однотипных случаев без дублирования кода.

---

## Часть 4: Как запустить тесты

### Установка зависимостей

```bash
# Сначала установите зависимости проекта (если ещё не установлены)
pip install aiogram pydantic-settings aiohttp fastapi uvicorn httpx

# Тестовые зависимости
pip install pytest pytest-asyncio pytest-cov
```

### Запуск всех тестов

```bash
# Из корня проекта
pytest tests/ -v
```

### Запуск с отчётом о покрытии

```bash
pytest tests/ -v --cov=. --cov-report=term-missing --cov-report=html
```

После этого откройте `htmlcov/index.html` в браузере — увидите какие строки
кода не покрыты тестами (выделены красным).

### Запуск одного файла

```bash
pytest tests/test_formatters.py -v
```

### Запуск одного теста

```bash
pytest tests/test_validators.py::TestValidateAuthor::test_single_word_fails -v
```

---

## Часть 5: Структура файлов тестов

```
project/
├── api/
│   └── client.py
├── config.py
├── handlers/
│   ├── admin.py
│   ├── catalog.py
│   └── ...
├── keyboards/
│   └── inline.py
├── states/
│   └── wizard.py
├── utils/
│   ├── formatters.py
│   ├── telegram.py
│   └── validators.py
├── webhook.py
├── main.py
└── tests/                    ← папка с тестами
    ├── pytest.ini            ← настройки pytest
    ├── conftest.py           ← общие фикстуры
    ├── test_formatters.py    ← тесты форматтеров
    ├── test_validators.py    ← тесты валидаторов
    ├── test_telegram_utils.py ← тесты utils/telegram.py
    ├── test_inline.py        ← тесты клавиатур
    ├── test_webhook.py       ← тесты webhook-эндпоинтов
    ├── test_client.py        ← тесты HTTP клиента
    └── test_config.py        ← тесты конфигурации
```

---

## Часть 6: Как тестируют клиенты (API Client)

Тестирование HTTP-клиента — отдельная история.

### Проблема

```python
# api/client.py
async def get_book(self, book_id: int):
    return await self._request("GET", f"/books/{book_id}")
```

Если запустить этот тест:
```python
async def test_get_book():
    result = await api.get_book(42)  # делает настоящий HTTP-запрос!
```

Это **плохо**:
- Требует запущенного сервера
- Зависит от данных в БД
- Медленно
- Нестабильно (сервер может упасть)

### Решение 1: Мокирование сессии

```python
# Подменяем aiohttp.ClientSession нашим муляжом
response = MagicMock()
response.status = 200
response.json = AsyncMock(return_value={"id": 42, "title": "Книга"})
response.__aenter__ = AsyncMock(return_value=response)
response.__aexit__ = AsyncMock(return_value=False)

api_client._session = MagicMock()
api_client._session.request = MagicMock(return_value=response)

result = await api_client.get_book(42)
assert result["id"] == 42
```

### Решение 2: `pytest-httpx` или `aioresponses`

```python
# Более элегантно с библиотекой aioresponses
from aioresponses import aioresponses

async def test_get_book():
    with aioresponses() as m:
        m.get("http://testserver/books/42", payload={"id": 42})
        result = await api.get_book(42)
    assert result["id"] == 42
```

### Что именно тестируем в клиенте?

Не логику бизнеса, а **поведение при разных HTTP-кодах**:

| HTTP статус | Ожидаемое поведение |
|------------|---------------------|
| 200 | Вернуть данные |
| 404 | Вернуть None |
| 400, 403, 422 | Бросить APIError с нужным status |
| 429 | Retry 2 раза, потом APIError(429) |
| 500, 503 | Retry с задержкой |
| Timeout | Retry 2 раза, потом RuntimeError |

И **входную валидацию** (то что не требует HTTP):
```python
await api.get_book(0)   # → None (без HTTP-запроса)
await api.get_book(-1)  # → None (без HTTP-запроса)
await api.get_books(query="a")  # → [] (слишком короткий запрос)
```

---

## Часть 7: Типичные ошибки новичков в тестировании

### ❌ Тест который всегда зелёный

```python
def test_format_book():
    text = format_book_card({"id": 1, "title": "T", "author": "A B", "status": "available"})
    assert isinstance(text, str)  # всегда True! Не проверяет ничего полезного
```

### ✅ Тест который проверяет реальное поведение

```python
def test_format_book_shows_title_and_author():
    text = format_book_card({"id": 1, "title": "Война и мир", "author": "Толстой Лев", "status": "available"})
    assert "Война и мир" in text
    assert "Толстой Лев" in text
    assert "Свободна" in text  # статус available = "Свободна"
```

### ❌ Один большой тест

```python
def test_everything():
    # 50 строк проверяющих всё подряд
    ...
```

### ✅ Много маленьких тестов

```python
def test_available_book_shows_green_emoji():  # один факт
def test_borrowed_book_shows_red_emoji():     # один факт
def test_book_xss_title_escaped():            # один факт
```

### ❌ Тест зависящий от другого теста

```python
def test_1():
    global shared_data
    shared_data = create_book()

def test_2():
    assert shared_data["id"] == 1  # упадёт если test_1 не запустился
```

### ✅ Каждый тест независим

```python
@pytest.fixture
def book():
    return create_book()

def test_something(book):  # фикстура создаётся заново для каждого теста
    assert book["id"] is not None
```
