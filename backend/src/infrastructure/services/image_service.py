import uuid
from PIL import Image
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import UploadFile
from pathlib import Path


class ImageService:
    def __init__(self, upload_dir: str = "media/books"):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        # Создаем пул потоков для CPU-задач (сжатие)
        self.executor = ThreadPoolExecutor(max_workers=4)

    async def process_and_save(self, file: UploadFile) -> str:
        """
        Асинхронно обрабатывает и сохраняет изображение.
        """
        file_extension = ".webp"
        filename = f"{uuid.uuid4()}{file_extension}"
        save_path = self.upload_dir / filename

        # Читаем данные файла асинхронно
        content = await file.read()

        # Запускаем тяжелую обработку в отдельном потоке,
        # чтобы не блокировать Event Loop (асинхронность в действии!)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self.executor,
            self._compress_image,
            content,
            save_path
        )

        return str(save_path)

    def _compress_image(self, content: bytes, save_path: Path):
        """
        Сама логика сжатия (выполняется в отдельном потоке).
        """
        from io import BytesIO
        with Image.open(BytesIO(content)) as img:
            # Конвертируем в RGB (на случай если был PNG с прозрачностью)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # Меняем размер, если картинка слишком огромная (например, 4K)
            max_size = (1080, 1080)
            img.thumbnail(max_size, Image.LANCZOS)

            # Сохраняем с оптимизацией
            img.save(save_path, "WEBP", quality=80, optimize=True)


image_service = ImageService()