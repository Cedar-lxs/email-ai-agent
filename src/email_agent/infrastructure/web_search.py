"""博查 Web Search 客户端，仅提供临时的外部通用技术参考。"""
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    snippet: str
    url: str


class BochaWebSearchClient:
    """受限的博查搜索客户端；外部结果不会写入本地索引。"""

    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled", False))
        self.api_key = os.getenv(str(config.get("api_key_env", "BOCHA_API_KEY")), "").strip()
        self.api_base = str(config.get("api_base", "https://api.bochaai.com")).rstrip("/")
        self.timeout = float(config.get("timeout", 15))
        self.max_results = min(5, max(1, int(config.get("max_results", 3))))
        self.retries = min(3, max(0, int(config.get("retries", 1))))

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    @staticmethod
    def _trusted_url(url: str) -> bool:
        host = urlparse(url).hostname or ""
        if not host or not url.startswith("https://"):
            return False
        return not host.endswith(("facebook.com", "instagram.com", "tiktok.com"))

    def search(self, query: str) -> list[WebSearchResult]:
        if not self.available or not query.strip():
            return []
        payload = None
        last_error = None
        for _ in range(self.retries + 1):
            try:
                response = httpx.post(
                    f"{self.api_base}/v1/web-search",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"query": query[:800], "count": self.max_results, "summary": True},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
        if payload is None:
            raise RuntimeError(f"博查 Web Search 请求失败：{last_error}") from last_error
        data = payload.get("data", payload)
        pages = data.get("webPages", {}).get("value", data.get("web_pages", data.get("results", [])))
        if not isinstance(pages, list):
            return []
        results = []
        for page in pages[:self.max_results]:
            if not isinstance(page, dict):
                continue
            title = str(page.get("name") or page.get("title") or "").strip()
            snippet = str(page.get("snippet") or page.get("summary") or page.get("content") or "").strip()
            url = str(page.get("url") or page.get("link") or "").strip()
            if title and snippet and self._trusted_url(url):
                results.append(WebSearchResult(title, snippet[:1200], url))
        return results
