"""Read-only cooking search fallback; results are explicitly search snippets."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from app.agent.tool_runtime import ToolResult
from app.core.config import Settings

SEARCH_URL = "https://html.duckduckgo.com/html/"


class SearchResults(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.current = None
        self.capture = None
        self.capture_tag = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if "result__a" in classes:
            href = urljoin(SEARCH_URL, attributes.get("href", ""))
            url = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            self.current = {"title": "", "snippet": "", "url": url}
            self.items.append(self.current)
            self.capture, self.capture_tag = "title", tag
        elif "result__snippet" in classes and self.current is not None:
            self.capture, self.capture_tag = "snippet", tag

    def handle_endtag(self, tag):
        if tag == self.capture_tag:
            self.capture = self.capture_tag = None

    def handle_data(self, data):
        if self.current is not None and self.capture:
            self.current[self.capture] += data


def parse_results(html: str, limit: int = 5) -> list[dict]:
    parser = SearchResults()
    parser.feed(html)
    evidence, seen = [], set()
    for item in parser.items:
        url = item["url"]
        parsed = urlparse(url)
        title = " ".join(item["title"].split())
        snippet = " ".join(item["snippet"].split())
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.hostname.endswith("duckduckgo.com") or not title or not snippet
                or url in seen):
            continue
        seen.add(url)
        evidence.append({
            "id": "web:" + hashlib.sha256(url.encode()).hexdigest()[:24],
            "kind": "web", "source_url": url, "title": title,
            "content": {"title": title, "name": title, "snippet": snippet[:1500],
                        "source_name": parsed.hostname, "evidence_type": "search_snippet"},
            "retrieved_at": datetime.now(UTC).isoformat(),
        })
        if len(evidence) >= limit:
            break
    return evidence


async def search_web(query: str, *, settings: Settings) -> ToolResult:
    if not settings.knowledge_web_search_enabled:
        return ToolResult(status="unavailable", error_code="WEB_SEARCH_DISABLED",
                          warnings=["本地技能库未命中，联网搜索尚未启用。"])
    try:
        async with httpx.AsyncClient(timeout=settings.knowledge_web_search_timeout_seconds,
                                     follow_redirects=True) as client:
            response = await client.get(SEARCH_URL, params={"q": query, "kl": "cn-zh"})
            response.raise_for_status()
        # DuckDuckGo returns 202 for its bot challenge, not for a successful empty search.
        if response.status_code != 200 or "anomaly.js" in response.text:
            return ToolResult(status="unavailable", error_code="WEB_SEARCH_BLOCKED")
        evidence = parse_results(response.text)
        if not evidence and "result--no-result" not in response.text:
            return ToolResult(status="unavailable", error_code="WEB_SEARCH_UNPARSEABLE")
    except httpx.HTTPError:
        # Preserve network outages as failures rather than claiming no matching knowledge.
        return ToolResult(status="unavailable", error_code="WEB_SEARCH_FAILED")
    return ToolResult(status="ok" if evidence else "no_result",
                      data={"evidence": evidence},
                      evidence_ids=[item["id"] for item in evidence],
                      warnings=["本地技能库未命中；网络资料仅为搜索摘要，未读取网页全文。"])
