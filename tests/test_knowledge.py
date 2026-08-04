import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.repositories import KnowledgeContextFormatter
from email_agent.infrastructure.knowledge.lexical import LexicalKnowledgeRetriever
from email_agent.infrastructure.knowledge.structured_json import glossary_entries, product_entries


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

    def test_structured_glossary_and_products_are_vector_ready(self):
        glossary = {"poe": [{"en": "PoE budget", "zh": "PoE总功率预算", "unit": "W", "context": "最大总功率"}]}
        products = {"switch": {"云网管系列": [{"model": "GPS208", "ports": 8, "poe_ports": 8, "speed": "全千兆"}]}}
        glossary_entry = glossary_entries(glossary)[0]
        product_entry = product_entries(products)[0]
        self.assertEqual(glossary_entry["metadata"]["kind"], "glossary")
        self.assertIn("英文术语: PoE budget", glossary_entry["answer"])
        self.assertIn("PoE总功率预算", glossary_entry["keywords"])
        self.assertEqual(product_entry["metadata"]["model"], "GPS208")
        self.assertIn("poe_ports: 8", product_entry["answer"])

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            (root_path / "products.vector.json").write_text(
                json.dumps({"schema_version": 1, "entries": [product_entry]}, ensure_ascii=False),
                encoding="utf-8",
            )
            retriever = LexicalKnowledgeRetriever(root_path)
            hits = retriever.search("GPS208有几个PoE端口", "技术参数咨询")
            self.assertTrue(hits)
            self.assertEqual(hits[0].metadata["identifiers"], ["gps208"])
            self.assertIn("poe_ports: 8", hits[0].content)



    def test_nested_knowledge_directories_are_loaded_with_relative_sources(self):
        with tempfile.TemporaryDirectory() as root:
            nested = Path(root, "switches", "poe")
            nested.mkdir(parents=True)
            Path(nested, "GPS208.md").write_text(
                "# GPS208\nGPS208 provides eight PoE ports.", encoding="utf-8"
            )
            retriever = LexicalKnowledgeRetriever(Path(root))
            hits = retriever.search("GPS208 PoE ports", "technical specification")
            self.assertTrue(hits)
            self.assertEqual(hits[0].source, "switches/poe/GPS208.md")
if __name__ == "__main__":
    unittest.main()
