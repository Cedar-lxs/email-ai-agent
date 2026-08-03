import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.models import IntentResult, RetrievalQuery
from email_agent.infrastructure.knowledge.hybrid import HybridKnowledgeRetriever
from email_agent.infrastructure.llm import AIProcessor


class ReplyPolicyTests(unittest.TestCase):
    def test_empty_first_reply_is_retried(self):
        processor = AIProcessor({})
        intent = IntentResult("故障排查", "neutral", "low", "设备离线", [], False, "en")
        responses = [
            "Technical Support",
            "Please confirm that the device has power and an active network connection.\n\nTechnical Support",
        ]
        with patch.object(processor, "_call_llm", side_effect=responses) as call:
            reply = processor.generate_reply("Device offline", "It is offline", intent, "依据")
        self.assertEqual(call.call_count, 2)
        self.assertIn("active network connection", reply)

    def test_incomplete_english_reply_is_retried(self):
        processor = AIProcessor({})
        intent = IntentResult("故障排查", "neutral", "low", "设备离线", [], False, "en")
        responses = [
            "First,",
            "Please check that the device is powered on and connected to the network.\n\nTechnical Support",
        ]
        with patch.object(processor, "_call_llm", side_effect=responses) as call:
            reply = processor.generate_reply("Device offline", "It is offline", intent, "依据")
        self.assertEqual(call.call_count, 2)
        self.assertIn("powered on", reply)

    def test_translation_returns_chinese_retrieval_text_and_preserves_model(self):
        processor = AIProcessor({})
        response = json.dumps({
            "subject_zh": "GPS208 设备离线",
            "body_zh": "添加 GPS208 后设备显示离线。",
            "technical_keywords": ["设备离线", "GPS208"],
        }, ensure_ascii=False)
        with patch.object(processor, "_call_llm", return_value=response):
            translated = processor.translate_for_retrieval(
                "GPS208 is offline", "The GPS208 appears offline after adding it."
            )
        self.assertEqual(translated["subject"], "GPS208 设备离线")
        self.assertIn("GPS208", translated["body"])
        self.assertIn("设备离线", translated["keywords"])
        self.assertIn("绑定设备", translated["keywords"])

    def test_chinese_reply_is_rewritten_to_english(self):
        processor = AIProcessor({})
        intent = IntentResult("故障排查", "neutral", "low", "设备离线", [], False, "en")
        responses = [
            "请检查设备是否通电并连接网络。\n\nTechnical Support",
            "Please check that the device is powered on and connected to the network.\n\nTechnical Support",
        ]
        with patch.object(processor, "_call_llm", side_effect=responses) as call:
            reply = processor.generate_reply("Device offline", "It is offline", intent, "依据")
        self.assertEqual(call.call_count, 2)
        self.assertFalse(AIProcessor._contains_chinese(reply))
        self.assertTrue(reply.endswith("Technical Support"))

    def test_signature_is_normalized_to_technical_support(self):
        original = "您好，火翼技术支持团队确认 GPS208 提供 8 个 POE 端口。\n\n火翼技术支持团队"
        normalized = AIProcessor._normalize_signature(original)
        self.assertEqual(normalized.count("Technical Support"), 2)
        self.assertTrue(normalized.endswith("Technical Support"))
        self.assertNotIn("火翼技术支持团队", normalized)

    def test_empty_reply_is_rejected_instead_of_becoming_signature_only(self):
        self.assertFalse(AIProcessor.has_reply_body("Technical Support"))
        self.assertFalse(AIProcessor.has_reply_body("\nTechnical Support\n"))
        self.assertTrue(AIProcessor.has_reply_body("请提供设备型号。\n\nTechnical Support"))


        with tempfile.TemporaryDirectory() as root:
            knowledge = Path(root)
            (knowledge / "base.md").write_text(
                "# 一般说明\n设备用于网络连接，具体参数需要查看对应型号。",
                encoding="utf-8",
            )
            retriever = HybridKnowledgeRetriever(
                knowledge,
                {"min_confidence": 0.75, "reranker": {"enabled": False}},
            )
            weak_hits = retriever.retrieve(RetrievalQuery("网络设备怎么用"), 3)
            self.assertEqual(weak_hits, [])

    def test_exact_model_evidence_can_pass_75_percent(self):
        with tempfile.TemporaryDirectory() as root:
            knowledge = Path(root)
            (knowledge / "base.md").write_text(
                "# GPS208 参数\nGPS208 提供 8 个 POE 端口。",
                encoding="utf-8",
            )
            retriever = HybridKnowledgeRetriever(
                knowledge,
                {"min_confidence": 0.75, "reranker": {"enabled": False}},
            )
            hits = retriever.retrieve(
                RetrievalQuery("有几个端口", subject="GPS208 参数",
                               identifiers=("gps208",)), 3
            )
            self.assertTrue(hits)
            self.assertGreaterEqual(hits[0].score, 0.75)


if __name__ == "__main__":
    unittest.main()
