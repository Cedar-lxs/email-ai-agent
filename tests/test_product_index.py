import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.models import MultimodalObservation
from email_agent.infrastructure.product_index import (
    ProductIndex,
    ProductRecord,
    detect_product_conflicts,
    format_product_context,
)


class ProductIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.knowledge_dir = Path(self.temp.name)
        structured_dir = self.knowledge_dir / "结构化数据"
        structured_dir.mkdir()
        (structured_dir / "products.vector.json").write_text(
            json.dumps({"documents": [{
                "question": "产品参数：GPS208",
                "answer": (
                    "产品型号: GPS208\n分类: 云网管系列\nports: 8\n"
                    "poe_ports: 8\nmanagement: 云管理"
                ),
                "section": "产品 > switch > 云网管系列 > GPS208",
                "metadata": {"kind": "product", "model": "GPS208"},
            }]}, ensure_ascii=False),
            encoding="utf-8",
        )
        markdown_dir = self.knowledge_dir / "03-云网管交换机"
        markdown_dir.mkdir()
        (markdown_dir / "GPS208.md").write_text(
            "---\nmodel: GPS208\nversion: V3.0\nmanaged: true\n---\n"
            "# GPS208 云网管交换机\n\n"
            "- **端口组成**：8个 POE RJ45 端口 + 2个千兆上联RJ45端口\n"
            "- **IEEE802.3at**：支持\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_lookup_merges_structured_and_markdown_facts_for_model_identifier(self):
        index = ProductIndex.from_knowledge(self.knowledge_dir)

        records = index.find(["GPS208"])

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.model, "GPS208")
        self.assertEqual(record.management_type, "managed")
        self.assertIn("products.vector.json", record.source)
        self.assertIn("8", " ".join(record.ports))
        self.assertIn("V3.0", record.versions)

    def test_context_includes_structured_product_facts(self):
        context = format_product_context([
            ProductRecord(
                model="GPS208", management_type="managed",
                ports=["8 PoE RJ45", "2 Gigabit uplink"], poe="802.3af/at",
                versions=["V3.0"], source="products.vector.json",
            )
        ])

        self.assertIn("GPS208", context)
        self.assertIn("managed", context)
        self.assertIn("8 PoE RJ45", context)

    def test_managed_type_disagreement_with_visual_observation_is_a_conflict(self):
        conflicts = detect_product_conflicts(
            [ProductRecord(model="GPS208", management_type="managed")],
            MultimodalObservation(
                model_numbers=["GPS208"], switch_management_type="unmanaged"
            ),
        )

        self.assertEqual(conflicts, ["product_conflict"])

    def test_chinese_unmanaged_value_remains_unmanaged_for_conflict_detection(self):
        conflicts = detect_product_conflicts(
            [ProductRecord(model="GS105", management_type="unmanaged")],
            MultimodalObservation(
                model_numbers=["GS105"], switch_management_type="非管理型"
            ),
        )

        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
