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
from email_agent.infrastructure.mail_sender import MailSender, normalize_customer_terms


class ReplyPolicyTests(unittest.TestCase):
    def test_overseas_app_terms_are_normalized(self):
        variants = (
            'Please use the WeChat Mini Program "Cloud Management".',
            "Open the WeChat mini-program to add the device.",
            "请使用微信小程序‘云网管’添加设备。",
        )
        for text in variants:
            normalized = normalize_customer_terms(text)
            self.assertIn("Amitres APP", normalized)
            self.assertNotRegex(normalized, r"(?i)wechat|微信|小程序")

    def test_generated_reply_uses_amitres_app(self):
        processor = AIProcessor({})
        intent = IntentResult("使用指导", "neutral", "low", "绑定设备", [], False, "en")
        generated = "Please add the device in the WeChat Mini Program.\n\nTechnical Support"
        with patch.object(processor, "_call_llm", return_value=generated):
            reply = processor.generate_reply("Add device", "How do I add it?", intent, "依据")
        self.assertIn("Amitres APP", reply)
        self.assertNotIn("WeChat", reply)

    def test_saved_draft_uses_amitres_app(self):
        with tempfile.TemporaryDirectory() as root:
            sender = MailSender("smtp.example.com", 465, "agent@example.com", "secret")
            path = sender.save_draft(
                "user@example.com", "Add device", "Use the WeChat Mini App.", root
            )
            self.assertIn("Amitres APP", Path(path).read_text(encoding="utf-8"))

    def test_smtp_send_uses_amitres_app(self):
        sender = MailSender("smtp.example.com", 465, "agent@example.com", "secret")
        with patch("email_agent.infrastructure.mail_sender.smtplib.SMTP_SSL") as smtp_class:
            smtp = smtp_class.return_value.__enter__.return_value
            self.assertTrue(sender.send_reply(
                "user@example.com", "Add device", "Use the WeChat Mini Program."
            ))
        message = smtp.send_message.call_args.args[0]
        body = message.get_payload(decode=True).decode(message.get_content_charset())
        self.assertIn("Amitres APP", body)
        self.assertNotIn("WeChat", body)

    def test_json_repair_handles_common_format_errors_without_retry(self):
        processor = AIProcessor({})
        payload = processor._load_json_with_retry(
            "```json\n{'intent': '故障排查', 'needs_human': false,}\n```",
            "意图识别", 300
        )
        self.assertEqual(payload["intent"], "故障排查")
        self.assertFalse(payload["needs_human"])

    def test_json_repair_retries_once_when_local_repair_fails(self):
        processor = AIProcessor({})
        with patch.object(processor, "_call_llm", return_value='{"intent":"网络连接"}') as call:
            payload = processor._load_json_with_retry("not json", "意图识别", 300)
        self.assertEqual(payload, {"intent": "网络连接"})
        call.assert_called_once()

    def test_json_repair_failure_is_safe(self):
        processor = AIProcessor({})
        with patch.object(processor, "_call_llm", return_value="still not json") as call:
            with self.assertRaisesRegex(ValueError, "JSON格式无效"):
                processor._load_json_with_retry("not json", "意图识别", 300)
        call.assert_called_once()

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
