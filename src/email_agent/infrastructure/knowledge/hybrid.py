"""BM25、向量召回、RRF 融合与可选本地重排。"""
import math
import re
from collections import Counter, defaultdict

from email_agent.domain.models import IndexStats, KnowledgeHit, RetrievalQuery
from email_agent.infrastructure.knowledge.loaders import KnowledgeBase


class HybridKnowledgeRetriever:
    def __init__(self, knowledge_dir, config: dict, embedding_client=None, vector_index=None):
        self.knowledge_dir = knowledge_dir
        self.config = config
        self.embedding_client = embedding_client
        self.vector_index = vector_index
        self.chunk_size = int(config.get("chunk_size", 900))
        self.chunk_overlap = int(config.get("chunk_overlap", 120))
        self.lexical_candidates = int(config.get("lexical_candidates", 25))
        self.vector_candidates = int(config.get("vector_candidates", 25))
        self.fusion_k = int(config.get("fusion_k", 60))
        self.min_confidence = float(config.get("min_confidence", 0.18))
        self.store = KnowledgeBase(str(knowledge_dir), self.chunk_size, self.chunk_overlap)
        self.last_trace = {"mode": "hybrid", "degraded_reason": "", "hits": []}
        self.last_rebuild = {}
        self.local_rerank_enabled = bool(config.get("reranker", {}).get("enabled", True))
        self._reranker = self._load_reranker(config.get("reranker", {}))
        self._build_bm25()

    @property
    def entries(self): return self.store.entries

    @property
    def load_errors(self): return self.store.load_errors

    def _build_bm25(self):
        self.documents, self.doc_freq = [], Counter()
        for entry in self.entries:
            tokens = self._tokens(f"{entry['question']} {entry['answer']}")
            counts = Counter(tokens)
            self.documents.append((entry, counts, len(tokens)))
            self.doc_freq.update(set(tokens))
        self.avg_length = sum(doc[2] for doc in self.documents) / max(1, len(self.documents))

    def search(self, query: str, intent: str = "", top_k: int = 3) -> list[KnowledgeHit]:
        return self.retrieve(RetrievalQuery(query, intent=intent), top_k)

    def retrieve(self, query: RetrievalQuery, top_k: int = 5) -> list[KnowledgeHit]:
        original_text = query.combined_text
        text = self._expand_query(original_text)
        identifiers = set(query.identifiers or self.store._identifiers(text))
        lexical = self._bm25(text, identifiers, self.lexical_candidates)
        vector, degraded = [], ""
        if self.embedding_client and self.vector_index and self.embedding_client.available:
            try:
                vector = self.vector_index.search(
                    self.embedding_client.embed_query(text), self.vector_candidates)
            except Exception as exc:
                degraded = f"向量召回不可用，已降级到 BM25: {exc}"
        else:
            degraded = "Embedding 未配置，已降级到 BM25"
        candidates = self._filter_identifiers(self._rrf(lexical, vector), identifiers)
        candidates, rerank_error = self._rerank(text, candidates)
        if rerank_error:
            degraded = "; ".join(value for value in (degraded, rerank_error) if value)
        hits = []
        scored_candidates = []
        scored_candidates = []
        for score, item, scores in candidates:
            exact_identifiers = identifiers & set(item.get("identifiers", []))
            confidence = self._confidence(score, scores)
            if exact_identifiers:
                confidence = max(confidence, 0.75 + min(0.2, 0.05 * len(exact_identifiers)))
                scores["identifier_matches"] = sorted(exact_identifiers)
            scored_candidates.append((confidence, item, scores))
        scored_candidates.sort(key=lambda row: row[0], reverse=True)
        hits = []
        for confidence, item, scores in scored_candidates:
            if confidence < self.min_confidence:
                continue
            metadata = {**item.get("metadata", {}), "chunk_id": item["chunk_id"],
                        "identifiers": item.get("identifiers", []), **scores,
                        "confidence": confidence}
            hits.append(KnowledgeHit(item["content"], item["source"], item["section"],
                                     confidence, metadata))
            if len(hits) >= top_k:
                break
        self.last_trace = {"mode": "hybrid" if vector else "bm25", "query": text,
                           "degraded_reason": degraded, "threshold": self.min_confidence,
                           "hits": [{"chunk_id": h.metadata.get("chunk_id"), "source": h.source,
                                     "section": h.section, "score": h.score} for h in hits],
                           "candidates": [
                               {"chunk_id": item.get("chunk_id"), "source": item.get("source"),
                                "section": item.get("section"), "score": confidence,
                                "lexical_score": scores.get("lexical_score"),
                                "vector_score": scores.get("vector_score"),
                                "accepted": confidence >= self.min_confidence}
                               for confidence, item, scores in scored_candidates[:10]
                           ]}
        return hits

    def _bm25(self, text: str, identifiers: set[str], limit: int):
        query_counts, total, scored = Counter(self._tokens(text)), len(self.documents), []
        for entry, counts, length in self.documents:
            score = 0.0
            for token, query_weight in query_counts.items():
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                frequency_docs = self.doc_freq[token]
                idf = math.log(1 + (total - frequency_docs + 0.5) / (frequency_docs + 0.5))
                denominator = frequency + 1.5 * (0.25 + 0.75 * length / max(1, self.avg_length))
                score += idf * frequency * 2.5 / denominator * min(query_weight, 2)
            entry_ids = set(entry["metadata"].get("identifiers", []))
            score += 12 * len(identifiers & entry_ids)
            if score > 0:
                item = {"chunk_id": entry["chunk_id"], "content": entry["answer"],
                        "source": entry["source"], "section": entry["section"],
                        "identifiers": list(entry_ids), "metadata": entry["metadata"]}
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:limit]

    def _rrf(self, lexical, vector):
        records = defaultdict(lambda: {"score": 0.0, "item": None, "scores": {}})
        for name, results in (("lexical", lexical), ("vector", vector)):
            for rank, (raw_score, item) in enumerate(results, 1):
                record = records[item["chunk_id"]]
                record["score"] += 1 / (self.fusion_k + rank)
                record["item"] = item
                record["scores"].update({f"{name}_score": float(raw_score), f"{name}_rank": rank})
        output = [(value["score"], value["item"],
                   {**value["scores"], "fusion_score": value["score"]})
                  for value in records.values()]
        return sorted(output, key=lambda row: row[0], reverse=True)

    @staticmethod
    def _filter_identifiers(candidates, identifiers: set[str]):
        if not identifiers:
            return candidates
        matched = [row for row in candidates if identifiers & set(row[1].get("identifiers", []))]
        return matched or candidates

    def _rerank(self, query: str, candidates):
        if not self._reranker or not candidates:
            return candidates, ""
        try:
            documents = [row[1]["content"] for row in candidates[:12]]
            for result in self._reranker.rerank(query, documents):
                if isinstance(result, dict):
                    index, score = int(result["index"]), float(result["score"])
                else:
                    index, score = int(result.index), float(result.score)
                candidates[index][2]["rerank_score"] = score
            return sorted(candidates, key=lambda row: row[2].get("rerank_score", -1),
                          reverse=True), ""
        except Exception as exc:
            return candidates, f"本地重排不可用，使用 RRF: {exc}"

    @staticmethod
    def _load_reranker(config):
        if not config.get("enabled", False): return None
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            return TextCrossEncoder(model_name=config.get("model"))
        except Exception:
            from email_agent.infrastructure.knowledge.reranker import TokenOverlapReranker
            return TokenOverlapReranker()

    @staticmethod
    def _confidence(_fusion_score, scores):
        lexical = 1.0 - math.exp(-scores.get("lexical_score", 0) / 18)
        vector = max(0.0, min(1.0, (scores.get("vector_score", 0) - 0.25) / 0.65))
        rerank = max(0.0, min(1.0, scores.get("rerank_score", 0)))
        if "rerank_score" in scores:
            return 0.25 * lexical + 0.25 * vector + 0.5 * rerank
        if "vector_score" in scores and "lexical_score" in scores:
            confidence = 0.45 * lexical + 0.55 * vector
            strongest_rank = max(int(scores.get("lexical_rank", 999)),
                                 int(scores.get("vector_rank", 999)))
            if strongest_rank <= 3:
                confidence += 0.09
            elif strongest_rank <= 5:
                confidence += 0.06
            elif strongest_rank <= 10:
                confidence += 0.03
            return min(0.95, confidence)
        return min(0.70, lexical if "lexical_score" in scores else vector)

    @staticmethod
    @staticmethod
    def _expand_query(text: str) -> str:
        value = text.lower()
        groups = (
            (("offline", "not online", "离线", "不在线"), "设备离线 设备不在线 绑定离线 云管理离线"),
            (("add device", "add the device", "adding device", "添加设备", "绑定设备"), "添加设备 绑定设备 设备绑定"),
            (("won't power on", "not power on", "no power", "无法开机", "不通电"),
             "无法开机 不能开机 不通电 指示灯不亮 电源"),
            (("no internet", "can't connect", "cannot connect", "无法上网", "无网络"),
             "无法上网 无网络 连接失败 网络不通"),
            (("port not working", "port failure", "端口无反应", "端口故障"),
             "端口无反应 端口不通 端口故障 指示灯"),
        )
        expansions = [addition for triggers, addition in groups
                      if any(trigger in value for trigger in triggers)]
        return f"{text}\n{' '.join(expansions)}" if expansions else text

    @staticmethod
    def _tokens(text: str) -> list[str]:
        text = text.lower()
        tokens = re.findall(r"[a-z][a-z0-9]*(?:[-_/\.][a-z0-9]+)*|\d+(?:\.\d+)?", text)
        for sequence in re.findall(r"[\u4e00-\u9fff]+", text):
            tokens.extend(sequence[i:i + 2] for i in range(max(0, len(sequence) - 1)))
            tokens.extend(sequence[i:i + 3] for i in range(max(0, len(sequence) - 2)))
        return tokens

    def rebuild(self) -> IndexStats:
        new_store = KnowledgeBase(str(self.knowledge_dir), self.chunk_size, self.chunk_overlap)
        if new_store.load_errors:
            return IndexStats(len(new_store.entries), len({e["source"] for e in new_store.entries}),
                              tuple(new_store.load_errors))
        if self.vector_index and self.embedding_client and self.embedding_client.available:
            try:
                self.last_rebuild = self.vector_index.sync(new_store.chunks)
            except Exception as exc:
                self.last_rebuild = {"status": "degraded", "error": str(exc),
                                     "preserved_vectors": self.vector_index.count()}
        self.store = new_store
        self._build_bm25()
        return IndexStats(len(self.entries), len({e["source"] for e in self.entries}))

    def get_stats(self) -> dict:
        stats = self.store.get_stats()
        stats.update({"mode": "hybrid", "last_rebuild": self.last_rebuild,
                      "vector": self.vector_index.status() if self.vector_index else {}})
        return stats
