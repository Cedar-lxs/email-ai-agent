"""RAG 索引构建、检索调试与离线质量评测。"""
import json
import re
from pathlib import Path

from email_agent.domain.models import RetrievalQuery


class RetrievalEvaluator:
    def __init__(self, retriever):
        self.retriever = retriever

    def generate_cases(self, limit: int = 100) -> list[dict]:
        cases = []
        seen = set()
        for entry in self.retriever.entries:
            query = self._evaluation_query(entry)
            if not query or query in seen:
                continue
            seen.add(query)
            cases.append({"query": query, "expected_chunk_id": entry["chunk_id"],
                          "expected_source": entry["source"], "section": entry["section"]})
            if len(cases) >= limit:
                break
        return cases

    @staticmethod
    def _evaluation_query(entry: dict) -> str:
        title = entry["question"].strip("# ").strip()
        if len(title) >= 3 and not RetrievalEvaluator._is_generic_title(title):
            return title
        content = re.sub(r"[`*_#>|]", " ", entry.get("answer", ""))
        content = re.sub(r"\s+", " ", content).strip()
        return content[:180] if len(content) >= 20 else ""

    @staticmethod
    def _is_generic_title(title: str) -> bool:
        lowered = title.lower()
        markers = ("faq", "sheet", "v2.0", "v1.0", ".docx", ".xlsx", ".pdf",
                   "d:\\", "文件存储路径", "更新计划", "知识包")
        return any(marker in lowered for marker in markers) or len(title) > 100

    def save_cases(self, path, limit: int = 100) -> list[dict]:
        cases = self.generate_cases(limit)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases),
                          encoding="utf-8")
        return cases

    def evaluate(self, cases: list[dict], top_k: int = 3) -> dict:
        hit1 = hit3 = source_hits = 0
        reciprocal = 0.0
        for case in cases:
            hits = self.retriever.retrieve(RetrievalQuery(case["query"]), top_k)
            ids = [hit.metadata.get("chunk_id") for hit in hits]
            sources = [hit.source for hit in hits]
            expected = case["expected_chunk_id"]
            if ids and ids[0] == expected:
                hit1 += 1
            if expected in ids[:3]:
                hit3 += 1
                reciprocal += 1 / (ids.index(expected) + 1)
            if case["expected_source"] in sources[:3]:
                source_hits += 1
        total = len(cases) or 1
        return {"cases": len(cases), "hit_at_1": hit1 / total, "hit_at_3": hit3 / total,
                "mrr": reciprocal / total, "source_hit_at_3": source_hits / total}

    @staticmethod
    def load_cases(path) -> list[dict]:
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
                if line.strip()]
