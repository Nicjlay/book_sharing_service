# infrastructure/services/image_service.py

import os
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import UploadFile
from pathlib import Path
from io import BytesIO
from PIL import Image


class ImageService:
    def __init__(self):
        # Используем переменную из docker-compose или дефолт /app/media
        self.media_base = Path(os.getenv("MEDIA_UPLOAD_DIR", "/app/media"))
        self.books_folder = "books"

        # Полный путь для сохранения: /app/media/books
        self.save_root = self.media_base / self.books_folder
        self.save_root.mkdir(parents=True, exist_ok=True)

        self.executor = ThreadPoolExecutor(max_workers=4)

    async def process_and_save(self, file: UploadFile) -> str:
        file_extension = ".webp"
        filename = f"{uuid.uuid4()}{file_extension}"

        # Полный путь для записи на диск
        absolute_save_path = self.save_root / filename

        # ПУТЬ ДЛЯ БД: убираем префикс "media/", оставляем только "books/..."
        # Это предотвратит дублирование media/media при генерации URL
        db_path = f"{self.books_folder}/{filename}"

        content = await file.read()
        if not content:
            raise ValueError("Empty file content")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self.executor,
            self._compress_image,
            content,
            absolute_save_path
        )

        return db_path

    def _compress_image(self, content: bytes, save_path: Path):
        try:
            with Image.open(BytesIO(content)) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                max_size = (1080, 1080)
                img.thumbnail(max_size, Image.LANCZOS)
                # Переводим Path в строку для Pillow
                img.save(str(save_path), "WEBP", quality=80, optimize=True)
        except Exception as e:
            # Важно: в Docker ошибка может быть из-за прав доступа (Permission denied)
            print(f"CRITICAL: Image processing error: {e}")
            raise e

image_service = ImageService()