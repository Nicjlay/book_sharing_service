# --- ЭТАП 1: Сборка (Builder) ---
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# --- ЭТАП 2: Финальный образ (Runtime) ---
FROM python:3.13-slim

# Устанавливаем системные зависимости (pg_dump)
# Делаем это под root (по умолчанию в образе)
RUN apt-get update && \
    apt-get install -y postgresql-client && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем библиотеки из билдера
COPY --from=builder /install /usr/local

# Копируем код
COPY src/ .

# Настройка безопасности
RUN useradd -m appuser && chown -R appuser /app
# Теперь переключаемся на обычного пользователя
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]