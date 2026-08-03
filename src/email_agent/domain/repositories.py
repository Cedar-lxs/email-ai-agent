"""应用层依赖的知识检索协议与上下文格式化。"""
from typing import Protocol

from email_agent.domain.models import IndexStats, KnowledgeHit, RetrievalQuery


class KnowledgeRetriever(Protocol):
    knowledge_dir: object
    load_errors: list[str]

    def search(self, query: str, intent: str = "", top_k: int = 3) -> list[KnowledgeHit]: ...

    def retrieve(self, query: RetrievalQuery, top_k: int = 3) -> list[KnowledgeHit]: ...

    def rebuild(self) -> IndexStats: ...


class KnowledgeContextFormatter:
    @staticmethod
    def format(hits: list[KnowledgeHit]) -> str:
        blocks = []
        for index, hit in enumerate(hits, 1):
            identifiers = ", ".join(hit.metadata.get("identifiers", []))
            identity = f" | 型号/标识: {identifiers}" if identifiers else ""
            blocks.append(
                f"[证据 {index} | 来源: {hit.source} | 章节: {hit.section}{identity}]\n"
                f"{hit.content}"
            )
        return "\n\n---\n\n".join(blocks)
