# --- ЭТАП 1: Сборка (Builder) ---
# Используем официальный образ Python. "slim" означает, что в нем нет лишнего мусора.
FROM python:3.13-slim AS builder

# Устанавливаем переменные окружения
# Не даем Python писать файлы .pyc (экономим место)
ENV PYTHONDONTWRITEBYTECODE=1
# Выключаем буферизацию логов (чтобы сразу видеть ошибки в консоли)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала копируем только список зависимостей.
# Если ты изменишь код, но не тронешь requirements, Docker возьмет установленные либы из кэша.
COPY requirements.txt .

# Устанавливаем зависимости в отдельную папку
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# --- ЭТАП 2: Финальный образ (Runtime) ---
FROM python:3.13-slim

WORKDIR /app

# Копируем только установленные библиотеки из первого этапа (builder)
COPY --from=builder /install /usr/local

# Копируем исходный код нашего приложения
COPY src/ .

# БЕЗОПАСНОСТЬ: Создаем пользователя, чтобы не запускать код от имени root (админа)
# Если хакер взломает твой сервис, он останется заперт внутри папки пользователя.
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Порт, который будет слушать FastAPI
EXPOSE 8000

# Команда для запуска (используем uvicorn для FastAPI)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]