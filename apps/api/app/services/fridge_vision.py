"""Call the local SAM 3 service. This process never loads the model."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.core.config import get_settings


class VisionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class VisionUnavailable(VisionError):
    def __init__(self, message: str):
        super().__init__("VISION_UNAVAILABLE", message)


class VisionRejected(VisionError):
    def __init__(self, message: str = "识别结果无法使用"):
        super().__init__("VISION_REJECTED", message)


def segment_image(data: bytes, content_type: str) -> dict:
    base = get_settings().fridge_vision_url.strip().rstrip("/")
    if not base:
        raise VisionUnavailable("未配置本地识别服务")
    request = urllib.request.Request(
        f"{base}/segment",
        data=data,
        method="POST",
        headers={"Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=get_settings().fridge_vision_timeout_seconds
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise VisionUnavailable("识别服务超时") from exc
    except urllib.error.URLError as exc:
        raise VisionUnavailable("识别服务未启动") from exc
    except json.JSONDecodeError as exc:
        raise VisionRejected() from exc
    return parse_detections(payload)


def parse_detections(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise VisionRejected()
    try:
        width = int(payload["width"])
        height = int(payload["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VisionRejected() from exc
    if width < 1 or height < 1 or width > 8000 or height > 8000:
        raise VisionRejected()
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > 30:
        raise VisionRejected()
    items = [item for raw in raw_items if (item := _item(raw)) is not None]
    return {"width": width, "height": height, "items": items}


def _item(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    prompt = str(raw.get("prompt") or "").strip()
    if not name or len(name) > 40 or not prompt or len(prompt) > 80:
        return None
    try:
        score = float(raw["score"])
        box = [float(value) for value in raw["box"]]
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= score <= 1 or len(box) != 4:
        return None
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    note = str(raw.get("note") or "")[:120]
    return {
        "name": name,
        "prompt": prompt,
        "score": round(score, 4),
        "box": [round(value, 4) for value in (x1, y1, x2, y2)],
        "needs_review": bool(raw.get("needs_review")) or score < 0.5 or "混淆" in note,
        "note": note,
    }
