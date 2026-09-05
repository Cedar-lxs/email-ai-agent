import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.email_service import EmailAgent
from email_agent.domain.models import (
    EmailMedia,
    IntentResult,
    KnowledgeHit,
    MultimodalObservation,
    ParsedEmail,
    StoredMedia,
)
from email_agent.infrastructure.database import EmailDB


class DummyRetriever:
    def __init__(self, hits=None):
        self.hits = hits
        self.queries = []
        self.last_trace = {"mode": "test", "hits": []}
        self.store = SimpleNamespace(_identifiers=self.identifiers)

    @staticmethod
    def identifiers(text):
        if "GS105" in text or "gs105" in text.lower():
            return ["gs105"]
        return []

    def retrieve(self, query, top_k):
        self.queries.append(query)
        if self.hits is not None:
            return self.hits
        return [
            KnowledgeHit(
                "GS105 offline troubleshooting evidence.",
                "base.md",
                "GS105",
                0.9,
                {"identifiers": ["gs105"]},
            )
        ]


class EmptyRetriever(DummyRetriever):
    def __init__(self):
        super().__init__(hits=[])


class MultimodalEmailServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp.name)
        self.db = EmailDB(str(self.temp_root / "emails.db"))

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def email(self):
        return ParsedEmail(
            message_id="m1",
            subject="GS105 offline",
            sender="customer@example.com",
            sender_name="Customer",
            body_text="The device appears offline. See photo.",
            body_html="",
            received_at="2026-09-03 10:00:00",
            media=[
                EmailMedia(
                    "img1", "fault.jpg", "image/jpeg", "attachment",
                    size_bytes=4, data=b"data",
                )
            ],
        )

    def agent(self, observation, mode="semi_auto"):
        agent = object.__new__(EmailAgent)
        agent.config = {
            "mail": {"account": "support@example.com"},
            "workflow": {
                "mode": mode,
                "auto_reply_types": ["网络连接"],
                "always_human_types": ["业务问题"],
            },
            "rag": {"min_confidence": 0.65},
            "multimodal": {"enabled": True},
        }
        agent.mode = mode
        agent.db = self.db
        agent.top_k = 3
        agent.draft_dir = str(self.temp_root / "drafts")
        agent.logger = Mock()
        agent.web_search = SimpleNamespace(available=False)
        agent.web_search_config = {"allowed_intents": ["网络连接"]}
        agent.retriever = DummyRetriever()
        agent.kb = agent.retriever
        stored = [
            StoredMedia(
                "img1", "fault.jpg", "image/jpeg", "attachment",
                str(self.temp_root / "fault.jpg"), 4,
            )
        ]
        agent.media_storage = SimpleNamespace(
            save=Mock(return_value=(stored, [])),
            manifest=Mock(return_value=[{
                "media_id": "img1",
                "filename": "fault.jpg",
                "content_type": "image/jpeg",
                "source": "attachment",
                "path": str(self.temp_root / "fault.jpg"),
                "size_bytes": 4,
            }]),
        )
        agent.multimodal_analyzer = SimpleNamespace(analyze_async=AsyncMock(return_value=observation))
        agent.ai = SimpleNamespace(
            analyze_intent_async=AsyncMock(return_value=IntentResult(
                "网络连接", "neutral", "low", "offline", ["offline"], False
            )),
            translate_for_retrieval_async=AsyncMock(return_value={
                "subject": "GS105 离线",
                "body": "设备显示离线",
                "keywords": ["设备离线"],
            }),
            generate_reply_async=AsyncMock(return_value="Please check the cable.\n\nTechnical Support"),
        )
        agent.sender = SimpleNamespace(
            save_draft=Mock(return_value=str(self.temp_root / "drafts" / "m1.txt")),
            send_reply=Mock(return_value=True),
            build_reply_subject=Mock(return_value="Re: GS105 offline"),
        )
        return agent

    async def test_semi_auto_analyzes_media_saves_trace_and_enriches_retrieval(self):
        observation = MultimodalObservation(
            summary="The label shows GS105 and the port indicator appears off.",
            product_identifiers=["GS105"],
            model_numbers=["GS105"],
            device_ids=["ID-7788"],
            port_composition=["5 x RJ45 Gigabit Ethernet"],
            versions=["Ver: 2.1"],
            switch_management_type="unmanaged",
            label_text=["Unmanaged Switch GS105 Ver: 2.1"],
            fault_signals=["port indicator off"],
            confidence=0.82,
        )
        agent = self.agent(observation)

        completed = await agent._process_email_async(self.email())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "draft_ready")
        self.assertEqual(self.db.parse_media_manifest(row)[0]["media_id"], "img1")
        self.assertEqual(
            self.db.parse_multimodal_trace(row)["summary"],
            "The label shows GS105 and the port indicator appears off.",
        )
        decision_trace = self.db.parse_decision_trace(row)
        self.assertEqual(decision_trace["action"], "draft_ready")
        self.assertIn("semi_auto_mode", decision_trace["blocking_reasons"])
        query = agent.retriever.queries[0]
        self.assertIn("GS105", query.keywords)
        self.assertIn("ID-7788", query.keywords)
        self.assertIn("5 x RJ45 Gigabit Ethernet", query.keywords)
        self.assertIn("Ver: 2.1", query.keywords)
        self.assertIn("非管理型交换机", query.keywords)
        self.assertIn("port indicator off", query.keywords)
        self.assertIn(
            "Visual observation",
            agent.ai.generate_reply_async.call_args.kwargs["multimodal_context"],
        )
        self.assertIn("gs105", query.identifiers)

    async def test_visual_risk_signals_escalate_before_reply_generation(self):
        observation = MultimodalObservation(
            summary="The housing appears burnt.",
            risk_signals=["burnt marks"],
            confidence=0.9,
        )
        agent = self.agent(observation)

        completed = await agent._process_email_async(self.email())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "escalated")
        self.assertIn("多模态识别发现高风险", row["notes"])
        decision_trace = self.db.parse_decision_trace(row)
        self.assertEqual(decision_trace["action"], "escalated")
        self.assertIn("visual_risk", decision_trace["blocking_reasons"])
        agent.ai.generate_reply_async.assert_not_called()

    async def test_semi_auto_media_analysis_error_still_creates_review_draft(self):
        agent = self.agent(MultimodalObservation(errors=["多模态分析失败：timeout"]))

        completed = await agent._process_email_async(self.email())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "draft_ready")
        self.assertIn("timeout", self.db.parse_multimodal_trace(row)["errors"][0])
        agent.sender.send_reply.assert_not_called()

    def test_full_auto_blocks_send_when_media_analysis_failed(self):
        agent = self.agent(MultimodalObservation(errors=["多模态分析失败：timeout"]), mode="full_auto")

        self.assertFalse(
            agent._can_auto_send(
                True,
                False,
                media_count=1,
                multimodal_observation=MultimodalObservation(errors=["多模态分析失败：timeout"]),
            )
        )

    async def test_full_auto_blocks_send_when_media_storage_error_leaves_no_stored_media(self):
        observation = MultimodalObservation()
        agent = self.agent(observation, mode="full_auto")
        agent.media_storage = SimpleNamespace(
            save=Mock(return_value=([], ["fault.jpg 超过图片大小限制"])),
            manifest=Mock(return_value=[]),
        )

        completed = await agent._process_email_async(self.email())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "draft_ready")
        self.assertIn("超过图片大小限制", self.db.parse_multimodal_trace(row)["errors"][0])
        agent.sender.send_reply.assert_not_called()

    def test_full_auto_allows_low_risk_send_when_media_confidence_is_low(self):
        agent = self.agent(MultimodalObservation(confidence=0.4), mode="full_auto")

        self.assertTrue(
            agent._can_auto_send(
                True,
                False,
                media_count=1,
                multimodal_observation=MultimodalObservation(confidence=0.4),
            )
        )

    async def test_low_risk_unknown_kb_with_visual_model_uses_bocha_and_auto_sends(self):
        observation = MultimodalObservation(
            summary="Label shows GS105 unmanaged switch.",
            product_identifiers=["GS105"],
            model_numbers=["GS105"],
            switch_management_type="unmanaged",
            confidence=0.92,
        )
        agent = self.agent(observation, mode="full_auto")
        agent.retriever = EmptyRetriever()
        agent.kb = agent.retriever
        agent.web_search = SimpleNamespace(
            available=True,
            search=Mock(return_value=[
                SimpleNamespace(
                    title="Switch troubleshooting",
                    snippet="Check Ethernet cable, link light, and upstream router.",
                    url="https://example.com/switch-guide",
                )
            ]),
        )
        agent.web_search_config = {
            "allowed_intents": ["网络连接"],
            "auto_send_low_risk": True,
        }

        completed = await agent._process_email_async(self.email())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "replied")
        self.assertIn("使用博查行业通用参考自动发送", row["notes"])
        decision_trace = self.db.parse_decision_trace(row)
        self.assertEqual(decision_trace["action"], "auto_sent")
        self.assertTrue(decision_trace["used_web_search"])
        agent.sender.send_reply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
