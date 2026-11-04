"""下厨房实时搜索:本地菜谱库未命中时,抓取站内搜索结果与步骤梗概。

只取两级数据:搜索结果页的菜名/链接/食材清单/评分,详情页 meta description
里的编号步骤梗概(搜索引擎摘要口径)。不抓取、不转载完整做法原文;所有结果
附来源链接。本模块任何异常由调用方兜底(返回空 → 走 LLM 通用做法)。
"""

from __future__ import annotations

import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_TIMEOUT = 10.0
_BASE = "https://www.xiachufang.com"
_REQUEST_INTERVAL = 0.8  # 对站点的最小请求间隔,避免触发限流
_last_request_at = 0.0
# 同关键词的搜索结果内存缓存(TTL 10 分钟):重复询问不重复抓取
_CACHE_TTL = 600.0
_CACHE_MAX_ENTRIES = 200  # 缓存条数上限：超出后先淘汰最早写入的关键词，防止无限增长
_search_cache: dict[str, tuple[float, list[dict]]] = {}


def _get(url: str) -> str:
    global _last_request_at
    wait = _REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()
    response = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _get_with_retry(url: str, attempts: int = 2) -> str:
    import time

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _get(url)
        except Exception as exc:  # noqa: BLE001 429/网络抖动退避后重试一次
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def search_xiachufang(keyword: str, limit: int = 3) -> list[dict]:
    """站内搜索:返回 [{title, url, ingredients, rating, cooked_count}]。

    同关键词结果做 10 分钟内存缓存,重复询问不重复抓取。"""
    term = keyword.strip()
    if not term:
        return []
    import time

    cached = _search_cache.get(term)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        html = _get_with_retry(_BASE + "/search/?keyword=" + urllib.parse.quote(term))
    except Exception:  # noqa: BLE001 网络/解析任何异常都视为无结果
        return []
    items: list[dict] = []
    # 结果页每条菜是一个 <div class="recipe recipe-215-horizontal …"> 块
    for block in re.split(r'<div class="recipe recipe-215-horizontal', html)[1:]:
        name = re.search(r'<p class="name">\s*<a href="(/recipe/\d+/)"[^>]*>\s*([^<]+?)\s*</a>', block)
        if not name:
            continue
        ing_block = re.search(r'<p class="ing ellipsis">(.*?)</p>', block, re.S)
        ingredients = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", ing_block.group(1))) if ing_block else ""
        rating = re.search(
            r"综合评分\s*(?:&nbsp;)?\s*<span class=\"score[^\"]*\">([\d.]+)</span>", block
        )
        cooked = re.search(r"<span class=\"bold score\">([\d.万]+)</span>", block)
        items.append(
            {
                "title": name.group(2).strip(),
                "url": _BASE + name.group(1),
                "ingredients": ingredients,
                "rating": rating.group(1) if rating else None,
                "cooked_count": cooked.group(1) if cooked else None,
            }
        )
        if len(items) >= limit:
            break
    if len(_search_cache) >= _CACHE_MAX_ENTRIES:
        oldest = min(_search_cache, key=lambda key: _search_cache[key][0])
        _search_cache.pop(oldest, None)
    _search_cache[term] = (time.monotonic(), items)
    return items


def fetch_step_brief(url: str, max_steps: int = 5, max_len: int = 40) -> list[str]:
    """详情页 meta description 里的编号步骤梗概。

    meta 格式为「【菜名】1.xxx;2.xxx;…」,但分隔符字符不统一,因此按
    「递增的数字编号」定位步骤边界(1. → 2. → 3.),不依赖具体分隔符;
    meta 偶发缺失时返回空,展示层容错。"""
    try:
        html = _get_with_retry(url)
    except Exception:  # noqa: BLE001 - unavailable detail pages contribute no optional step brief
        return []
    meta = re.search(r'<meta name="description" content="([^"]+)"', html)
    if not meta:
        return []
    text = re.sub(r"^【[^】]*】", "", meta.group(1))
    # 递增编号定位:1. → 2. → 3. …(跳过 "0.5茶匙" 这类非编号数字)
    marks: list[int] = []
    expected = 1
    for match in re.finditer(r"(\d+)[.、]", text):
        if int(match.group(1)) == expected:
            marks.append(match.start())
            expected += 1
        if expected > max_steps:
            break
    if not marks:
        return []
    bounds = [*marks, len(text)]
    briefs: list[str] = []
    for i, start in enumerate(bounds[:-1]):
        step = re.sub(r"^\d+[.、]\s*", "", text[start : bounds[i + 1]].strip())
        step = re.sub(r"\s+", " ", step).strip(" ,，、-—;;\uFF1B.")
        if step:
            briefs.append(step[:max_len])
    return briefs


def search_with_briefs(keyword: str, limit: int = 3) -> list[dict]:
    """搜索 + 并发抓取步骤梗概;单条详情失败不影响其余结果。"""
    items = search_xiachufang(keyword, limit)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(limit, 3)) as pool:
        futures = {pool.submit(fetch_step_brief, item["url"]): item for item in items}
        for future, item in futures.items():
            try:
                item["steps_brief"] = future.result(timeout=15)
            except Exception:  # noqa: BLE001 - keep other results when one detail fails
                item["steps_brief"] = []
    return items
