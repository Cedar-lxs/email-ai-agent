"""OpenAI 兼容 Embedding 客户端，适配阿里云百炼 DashScope。"""
import time

import httpx


class OpenAICompatibleEmbeddingClient:
    def __init__(self, api_key: str, base_url: str, model: str,
                 dimensions: int = 1024, batch_size: int = 10,
                 timeout: float = 60, retries: int = 3):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = int(dimensions)
        self.batch_size = min(max(1, int(batch_size)), 10)
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.disabled_reason = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model and not self.disabled_reason)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.available:
            reason = self.disabled_reason or "Embedding API 未配置"
            raise RuntimeError(reason)
        output = []
        for start in range(0, len(texts), self.batch_size):
            output.extend(self._request(texts[start:start + self.batch_size]))
        return output

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def _request(self, texts: list[str]) -> list[list[float]]:
        error = None
        for attempt in range(self.retries):
            try:
                response = httpx.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json={"model": self.model, "input": texts,
                          "dimensions": self.dimensions, "encoding_format": "float"},
                    timeout=self.timeout,
                )
                if response.status_code in {401, 403}:
                    self.disabled_reason = (
                        f"Embedding 鉴权失败（HTTP {response.status_code}），"
                        "本进程已停止向量请求并降级到 BM25"
                    )
                    raise RuntimeError(self.disabled_reason)
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                vectors = [item["embedding"] for item in data]
                if len(vectors) != len(texts):
                    raise RuntimeError("Embedding 返回数量与输入不一致")
                if any(len(vector) != self.dimensions for vector in vectors):
                    raise RuntimeError("Embedding 返回维度与配置不一致")
                return vectors
            except RuntimeError as exc:
                error = exc
                if self.disabled_reason:
                    break
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                error = exc
            if attempt + 1 < self.retries:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Embedding API 调用失败: {error}") from error
