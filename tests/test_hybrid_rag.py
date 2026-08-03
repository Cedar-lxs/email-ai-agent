import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.models import RetrievalQuery
from email_agent.infrastructure.knowledge.embeddings import OpenAICompatibleEmbeddingClient
from email_agent.infrastructure.knowledge.hybrid import HybridKnowledgeRetriever
from email_agent.infrastructure.knowledge.loaders import KnowledgeBase
from email_agent.infrastructure.knowledge.vector_index import SQLiteVectorIndex


class FakeEmbedding:
    model = "fake-embedding"
    available = True

    def __init__(self):
        self.calls = 0
        self.fail = False

    @staticmethod
    def _vector(text):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [value / 255 for value in digest[:16]]

    def embed(self, texts):
        if self.fail:
            raise RuntimeError("simulated embedding failure")
        self.calls += len(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        if self.fail:
            raise RuntimeError("simulated embedding failure")
        return self._vector(text)


class HybridRagTests(unittest.TestCase):
    def test_version_numbers_are_not_treated_as_product_models(self):
        identifiers = KnowledgeBase._identifiers("GS108 V1 failed, FAQ1.3 Q1")
        self.assertIn("gs108", identifiers)
        self.assertNotIn("v1", identifiers)
        self.assertNotIn("q1", identifiers)

    def test_version_numbers_are_not_treated_as_product_models(self):
        identifiers = KnowledgeBase._identifiers("GS108 V1 failed, FAQ1.3 Q1")
        self.assertIn("gs108", identifiers)
        self.assertNotIn("v1", identifiers)
        self.assertNotIn("q1", identifiers)

    def test_vector_search_ignores_rows_from_other_embedding_models(self):
        with tempfile.TemporaryDirectory() as root:
            index = SQLiteVectorIndex(Path(root) / "vectors.db", FakeEmbedding())
            with index.conn:
                index.conn.execute("""
                    INSERT INTO vectors
                        (chunk_id, content_hash, source, section, content, identifiers_json,
                         metadata_json, model, dimensions, vector, updated_at)
                    VALUES ('old', 'hash', 'old.md', 'old', 'old content', '[]', '{}',
                            'old-model', 16, ?, '2026-01-01')
                """, (bytes(16 * 4),))
            self.assertEqual(index.count(), 0)
            self.assertEqual(index.search([1.0] * 16), [])
            index.close()

    def test_incremental_cache_delete_failure_and_hybrid_retrieval(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            knowledge = root / "knowledge"
            knowledge.mkdir()
            document = knowledge / "products.md"
            document.write_text(
                "# GPS208 参数\nGPS208 提供 8 个 POE 端口。\n\n"
                "# GPS210 参数\nGPS210 提供 10 个 POE 端口。",
                encoding="utf-8",
            )
            embedding = FakeEmbedding()
            index = SQLiteVectorIndex(root / "vectors.db", embedding)
            config = {"chunk_size": 300, "chunk_overlap": 30, "min_confidence": 0.05,
                      "lexical_candidates": 10, "vector_candidates": 10,
                      "reranker": {"enabled": False}}
            retriever = HybridKnowledgeRetriever(knowledge, config, embedding, index)
            first = retriever.rebuild()
            first_calls = embedding.calls
            second = retriever.rebuild()
            self.assertEqual(first.entries, second.entries)
            self.assertGreater(first_calls, 0)
            self.assertEqual(embedding.calls, first_calls)

            hits = retriever.retrieve(
                RetrievalQuery("有几个POE端口", subject="GPS208端口参数",
                               keywords=("GPS208",), identifiers=("gps208",)), 3
            )
            self.assertTrue(hits)
            self.assertIn("8 个 POE", hits[0].content)
            self.assertEqual(hits[0].source, "products.md")

            preserved = index.count()
            document.write_text("# GPS208 参数\nGPS208 提供 8 个 POE 端口并支持告警。",
                                encoding="utf-8")
            embedding.fail = True
            retriever.rebuild()
            self.assertEqual(index.count(), preserved)
            self.assertEqual(retriever.last_rebuild["status"], "degraded")

            embedding.fail = False
            retriever.rebuild()
            self.assertEqual(index.count(), 1)
            index.close()

    def test_english_offline_query_is_expanded_with_chinese_technical_terms(self):
        expanded = HybridKnowledgeRetriever._expand_query(
            "Device not online. Add the device to display offline."
        )
        self.assertIn("设备离线", expanded)
        self.assertIn("绑定设备", expanded)
        self.assertIn("云管理离线", expanded)


    def test_confidence_requires_two_retrieval_signals_without_model_id(self):
        lexical_only = HybridKnowledgeRetriever._confidence(
            0.01, {"lexical_score": 60.0, "lexical_rank": 1}
        )
        vector_only = HybridKnowledgeRetriever._confidence(
            0.01, {"vector_score": 0.9, "vector_rank": 1}
        )
        dual_signal = HybridKnowledgeRetriever._confidence(
            0.03, {"lexical_score": 53.0, "lexical_rank": 1,
                   "vector_score": 0.66, "vector_rank": 1}
        )
        self.assertLess(lexical_only, 0.75)
        self.assertLess(vector_only, 0.75)
        self.assertGreaterEqual(dual_signal, 0.75)


    @patch("email_agent.infrastructure.knowledge.embeddings.httpx.post")
    def test_auth_failure_disables_further_requests(self, post):
        response = Mock(status_code=401, text='{"code":"invalid_api_key"}')
        post.return_value = response
        client = OpenAICompatibleEmbeddingClient("invalid", "https://example.test/v1", "model",
                                                  dimensions=2, retries=3)
        with self.assertRaisesRegex(RuntimeError, "鉴权失败"):
            client.embed(["test"])
        self.assertFalse(client.available)
        self.assertEqual(post.call_count, 1)
        with self.assertRaisesRegex(RuntimeError, "鉴权失败"):
            client.embed_query("again")
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
