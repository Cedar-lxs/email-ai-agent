"""兼容的关键词检索实现。"""
from email_agent.domain.models import IndexStats, KnowledgeHit
from email_agent.infrastructure.knowledge.loaders import KnowledgeBase


class LexicalKnowledgeRetriever:
    def __init__(self, knowledge_dir, chunk_size: int = 900, chunk_overlap: int = 120):
        self.knowledge_dir = knowledge_dir
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.store = KnowledgeBase(str(knowledge_dir), chunk_size, chunk_overlap)

    @property
    def entries(self):
        return self.store.entries

    @property
    def load_errors(self):
        return self.store.load_errors

    def search(self, query: str, intent: str = "", top_k: int = 3) -> list[KnowledgeHit]:
        if not query.strip() or top_k <= 0:
            return []
        identifiers = self.store._identifiers(query)
        terms = self.store._chinese_terms(query)
        scored = []
        for entry in self.entries:
            text = f"{entry['question']}\n{entry['answer']}".lower()
            score = sum(40 for value in identifiers if value in text)
            score += sum(8 if term in entry["question"].lower() else 3
                         for term in terms if term in text)
            if intent and intent.lower() in text:
                score += 5
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [KnowledgeHit(entry["answer"], entry["source"], entry["section"], score,
                             {"chunk_id": entry["chunk_id"],
                              "identifiers": entry["metadata"].get("identifiers", [])})
                for score, entry in scored[:top_k]]

    def retrieve(self, query, top_k: int = 3) -> list[KnowledgeHit]:
        return self.search(query.combined_text, query.intent, top_k)

    def rebuild(self) -> IndexStats:
        self.store = KnowledgeBase(str(self.knowledge_dir), self.chunk_size, self.chunk_overlap)
        stats = self.store.get_stats()
        return IndexStats(stats["entries"], stats["sources"], tuple(stats["errors"]))

    def get_stats(self) -> dict:
        return self.store.get_stats()
