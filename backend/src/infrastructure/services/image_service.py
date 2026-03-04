import asyncio
import logging
import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from io import BytesIO
from typing import Optional
from PIL import Image

from fastapi import UploadFile, HTTPException

from utils import get_env_int

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

MAGIC_BYTES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff",      "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF",              "image/webp"),
    (b"GIF87a",            "image/gif"),
    (b"GIF89a",            "image/gif"),
]

_MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", str(25_000_000)))
Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS


class ImageProcessingError(ValueError):
    """Ошибка при обработке/сжатии изображения в потоке."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _detect_mime(content: bytes) -> str | None:
    """Определяет MIME-тип по первым байтам файла."""
    for magic, mime in MAGIC_BYTES:
        if content[: len(magic)] == magic:
            if mime == "image/webp" and content[8:12] != b"WEBP":
                continue
            return mime
    return None


class ImageService:
    def __init__(self):
        self.media_base   = Path(os.getenv("MEDIA_UPLOAD_DIR", "/app/media")).resolve()
        self.books_folder = "books"
        self.save_root    = self.media_base / self.books_folder
        self.save_root.mkdir(parents=True, exist_ok=True)
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._workers = get_env_int("IMAGE_WORKERS", default=4, min_val=1, max_val=32)
        self.executor = ThreadPoolExecutor(max_workers=self._workers)

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._workers)
        return self._semaphore

    async def process_and_save(self, file: UploadFile) -> str:
        if file.content_type and file.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"Неподдерживаемый тип файла: {file.content_type}. "
                    f"Разрешены: {', '.join(ALLOWED_MIME_TYPES)}"
                ),
            )

        content = await file.read(MAX_FILE_SIZE + 1)
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Файл слишком большой. Максимум: {MAX_FILE_SIZE // 1024 // 1024} МБ",
            )
        if not content:
            raise HTTPException(status_code=400, detail="Пустой файл")

        detected_mime = _detect_mime(content)
        if detected_mime not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Файл не является допустимым изображением (проверка содержимого не прошла)",
            )

        filename           = f"{uuid.uuid4()}.webp"
        absolute_save_path = self.save_root / filename
        db_path            = f"{self.books_folder}/{filename}"

        async with self._get_semaphore():
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(
                    self.executor,
                    self._compress_image,
                    content,
                    absolute_save_path,
                )
            except ImageProcessingError as e:
                raise HTTPException(status_code=e.status_code, detail=str(e))
            except Exception as e:
                logger.error("Image processing failed: %s", e)
                raise HTTPException(
                    status_code=422,
                    detail=f"Не удалось обработать изображение: {e}",
                )

        return db_path

    def _compress_image(self, content: bytes, save_path: Path) -> None:
        """
        Синхронная обработка изображения в потоке.
        Атомарная запись через временный файл + os.replace().
        """
        tmp_path: Path | None = None
        try:
            fd, tmp_str = tempfile.mkstemp(
                suffix=".webp.tmp",
                dir=save_path.parent,
            )
            tmp_path = Path(tmp_str)
            os.close(fd)

            with Image.open(BytesIO(content)) as img:
                if img.width * img.height > _MAX_IMAGE_PIXELS:
                    raise ImageProcessingError(
                        f"Изображение слишком большое: {img.width}×{img.height} пикселей. "
                        f"Максимум: {_MAX_IMAGE_PIXELS:,} пикселей.",
                        status_code=422,
                    )
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.thumbnail((1080, 1080), Image.LANCZOS)
                img.save(str(tmp_path), "WEBP", quality=80, optimize=True)

            os.replace(tmp_path, save_path)
            tmp_path = None

        except ImageProcessingError:
            raise
        except Exception as e:
            logger.error("Image processing error: %s", e)
            raise ImageProcessingError(f"Внутренняя ошибка обработки: {e}")
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def delete_image(self, db_path: str) -> bool:
        """
        Синхронное удаление файла изображения по db_path (относительный путь).
        Защита от path traversal через resolve() + relative_to().
        """
        if not db_path or db_path == "books/base_cover.jpg":
            return False

        if not db_path.startswith(self.books_folder + "/"):
            logger.warning(
                "delete_image: rejected path outside '%s/': %s",
                self.books_folder, db_path,
            )
            return False

        try:
            abs_path = (self.media_base / db_path).resolve()
            abs_path.relative_to(self.media_base)
        except ValueError:
            logger.warning("delete_image: path traversal attempt blocked: %s", db_path)
            return False
        except Exception as e:
            logger.warning("delete_image: path resolution error for %s: %s", db_path, e)
            return False

        try:
            abs_path.unlink()
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.warning("delete_image(%s): %s", db_path, e)
            return False

    async def adelete_image(self, db_path: str) -> bool:
        """Асинхронная обёртка над delete_image — не блокирует event loop."""
        return await asyncio.to_thread(self.delete_image, db_path)

    def close(self) -> None:
        """Корректное освобождение ThreadPoolExecutor при shutdown."""
        self.executor.shutdown(wait=True, cancel_futures=True)


image_service = ImageService()
