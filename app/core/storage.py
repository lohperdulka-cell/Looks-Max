"""Хранилище файлов: валидация magic bytes, безопасные имена, изоляция по пользователю."""

from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.config import settings

# Сигнатуры форматов (magic bytes) — проверяем ДО передачи в Pillow/OpenCV.
MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/webp": [b"RIFF"],  # дополнительно проверяем "WEBP" на смещении 8
}


@dataclass(slots=True)
class StoredFile:
    """Метаданные сохранённого файла."""

    relative_path: str
    absolute_path: Path
    content_type: str
    size_bytes: int
    width: int
    height: int
    checksum: str


def _sniff_magic(head: bytes) -> str | None:
    """Определяет MIME по magic bytes. Возвращает None, если формат не распознан."""
    for mime, sigs in MAGIC_SIGNATURES.items():
        for sig in sigs:
            if head.startswith(sig):
                if mime == "image/webp" and head[8:12] != b"WEBP":
                    continue
                return mime
    return None


def validate_image_bytes(data: bytes) -> str:
    """Проверяет magic bytes и реальную декодируемость изображения.

    Возвращает MIME. Бросает HTTPException 400 при несоответствии.
    """
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")

    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл больше {settings.max_upload_mb} МБ",
        )

    mime = _sniff_magic(data[:16])
    if mime is None or mime not in settings.allowed_image_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Недопустимый формат. Разрешены JPEG, PNG, WebP",
        )

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл повреждён или не является изображением",
        ) from exc

    return mime


def _safe_suffix(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".bin")


async def save_user_image(upload: UploadFile, user_id: int) -> StoredFile:
    """Сохраняет загруженное изображение в изолированную директорию пользователя.

    Имя генерируется случайно (UUID) — оригинальное имя не используется в пути,
    что исключает path traversal и коллизии.
    """
    raw = await upload.read()
    content_type = validate_image_bytes(raw)

    user_dir = settings.media_root / "users" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{_safe_suffix(content_type)}"
    abs_path = user_dir / filename

    # Пишем во временный файл и переименовываем атомарно.
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".tmp")
    tmp_path.write_bytes(raw)
    tmp_path.replace(abs_path)

    with Image.open(io.BytesIO(raw)) as img:
        width, height = img.size

    relative = abs_path.relative_to(settings.media_root).as_posix()
    checksum = hashlib.sha256(raw).hexdigest()

    return StoredFile(
        relative_path=relative,
        absolute_path=abs_path,
        content_type=content_type,
        size_bytes=len(raw),
        width=width,
        height=height,
        checksum=checksum,
    )


def resolve_media_path(relative_path: str) -> Path:
    """Безопасно резолвит путь внутри media_root, отклоняя выход за его пределы.

    Проверка через is_relative_to, а не startswith(str(root)): строковый префикс
    пропускает соседние каталоги с общим префиксом («/media_evil» начинается с
    «/media»), и путь вида «../media_evil/x» уводил бы запись/чтение за пределы
    media_root, если значение когда-нибудь придёт из БД/API без валидации.
    """
    root = settings.media_root.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный путь")
    return candidate


def delete_media(relative_path: str) -> None:
    """Удаляет файл, если он существует (используется при GDPR-удалении аккаунта)."""
    try:
        path = resolve_media_path(relative_path)
    except HTTPException:
        return
    path.unlink(missing_ok=True)
