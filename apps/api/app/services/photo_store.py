"""Strip metadata from cooking photos and store them under a configurable root."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import get_settings

MAX_BYTES = 8 * 1024 * 1024
JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
PNG_METADATA = {b"eXIf", b"tEXt", b"iTXt", b"zTXt"}


class PhotoRejected(ValueError):
    pass


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise PhotoRejected("not a jpeg")
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise PhotoRejected("bad jpeg marker")
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            out += bytes((0xFF, marker))
            continue
        if i + 2 > len(data):
            raise PhotoRejected("truncated jpeg segment")
        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2 or i + length > len(data):
            raise PhotoRejected("bad jpeg segment")
        if marker != 0xE1:  # APP1 carries EXIF/XMP.
            out += bytes((0xFF, marker)) + data[i : i + length]
        i += length
        if marker == 0xDA:
            out += data[i:]
            return bytes(out)
    raise PhotoRejected("jpeg has no image data")


def _strip_png(data: bytes) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise PhotoRejected("not a png")
    out = bytearray(signature)
    i = len(signature)
    while i + 12 <= len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        kind = data[i + 4 : i + 8]
        end = i + 12 + length
        if end > len(data):
            raise PhotoRejected("truncated png chunk")
        if kind not in PNG_METADATA:
            out += data[i:end]
        i = end
        if kind == b"IEND":
            return bytes(out)
    raise PhotoRejected("png has no IEND")


def sanitize(data: bytes) -> tuple[bytes, str, str]:
    if not data:
        raise PhotoRejected("empty file")
    if len(data) > MAX_BYTES:
        raise PhotoRejected("file too large")
    if data.startswith(b"\xff\xd8"):
        return _strip_jpeg(data), "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _strip_png(data), "image/png", "png"
    raise PhotoRejected("only jpeg and png are accepted")


def path_for(key: str) -> Path:
    root = Path(get_settings().cooking_photo_dir).resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise PhotoRejected("invalid storage key")
    return path


def store(data: bytes, *, prefix: str = "") -> dict:
    clean, content_type, ext = sanitize(data)
    digest = hashlib.sha256(clean).hexdigest()
    folder = prefix.strip("/")
    key = f"{folder}/{digest[:2]}/{digest}.{ext}" if folder else f"{digest[:2]}/{digest}.{ext}"
    path = path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    if created:
        path.write_bytes(clean)
    return {
        "storage_key": key,
        "content_type": content_type,
        "byte_size": len(clean),
        "sha256": digest,
        "created": created,
    }


def remove(key: str) -> None:
    try:
        path_for(key).unlink(missing_ok=True)
    except PhotoRejected:
        return