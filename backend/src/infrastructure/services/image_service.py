import os
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import UploadFile, HTTPException
from pathlib import Path
from io import BytesIO
from PIL import Image


# FIX #4: максимальный размер файла — 10 МБ. Читаем на 1 байт больше,
# чтобы не тянуть гигабайтные файлы в память даже частично.
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# FIX #5: разрешённые MIME-типы изображений
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# FIX #5: «магические байты» для дополнительной проверки на уровне контента
# (content-type от клиента легко подделать)
MAGIC_BYTES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),   # RIFF....WEBP
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def _detect_mime(content: bytes) -> str | None:
    """Определяет MIME-тип по первым байтам файла."""
    for magic, mime in MAGIC_BYTES:
        if content[:len(magic)] == magic:
            # Для WebP нужен дополнительный контроль: RIFF????WEBP
            if mime == "image/webp" and content[8:12] != b"WEBP":
                continue
            return mime
    return None


class ImageService:
    def __init__(self):
        self.media_base   = Path(os.getenv("MEDIA_UPLOAD_DIR", "/app/media"))
        self.books_folder = "books"

        # Полный путь для сохранения: /app/media/books
        self.save_root = self.media_base / self.books_folder
        self.save_root.mkdir(parents=True, exist_ok=True)

        # FIX #18: Semaphore ограничивает одновременную обработку изображений,
        # предотвращая исчерпание памяти при пиковой нагрузке.
        self._semaphore = asyncio.Semaphore(4)
        self.executor   = ThreadPoolExecutor(max_workers=4)

    async def process_and_save(self, file: UploadFile) -> str:
        # FIX #5: проверяем Content-Type до чтения тела
        if file.content_type and file.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Неподдерживаемый тип файла: {file.content_type}. "
                       f"Разрешены: {', '.join(ALLOWED_MIME_TYPES)}",
            )

        # FIX #4: читаем не более MAX+1 байт, чтобы не держать весь огромный файл в памяти
        content = await file.read(MAX_FILE_SIZE + 1)
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Файл слишком большой. Максимум: {MAX_FILE_SIZE // 1024 // 1024} МБ",
            )
        if not content:
            raise HTTPException(status_code=400, detail="Пустой файл")

        # FIX #5: верифицируем содержимое по магическим байтам — Content-Type легко подделать
        detected_mime = _detect_mime(content)
        if detected_mime not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Файл не является допустимым изображением (проверка содержимого не прошла)",
            )

        filename            = f"{uuid.uuid4()}.webp"
        absolute_save_path  = self.save_root / filename
        db_path             = f"{self.books_folder}/{filename}"

        # FIX #18: Semaphore + executor ограничивают параллельную обработку
        async with self._semaphore:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                self._compress_image,
                content,
                absolute_save_path,
            )

        return db_path

    def _compress_image(self, content: bytes, save_path: Path):
        try:
            with Image.open(BytesIO(content)) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.thumbnail((1080, 1080), Image.LANCZOS)
                img.save(str(save_path), "WEBP", quality=80, optimize=True)
        except Exception as e:
            print(f"CRITICAL: Image processing error: {e}")
            raise


image_service = ImageService()