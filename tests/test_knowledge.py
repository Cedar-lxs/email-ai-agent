import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.repositories import KnowledgeContextFormatter
from email_agent.infrastructure.knowledge.lexical import LexicalKnowledgeRetriever


class KnowledgeTests(unittest.TestCase):
    def test_lexical_hits_have_rag_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "product.md").write_text("# GPS208\nGPS208 提供 8 个 POE 端口。", encoding="utf-8")
            retriever = LexicalKnowledgeRetriever(Path(root))
            hits = retriever.search("GPS208有几个POE端口", "技术参数咨询")
            self.assertTrue(hits)
            self.assertEqual(hits[0].source, "product.md")
            self.assertIn("GPS208", KnowledgeContextFormatter.format(hits))
            self.assertEqual(retriever.rebuild().sources, 1)


if __name__ == "__main__":
    unittest.main()
