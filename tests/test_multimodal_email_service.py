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
    def __init__(self):
        self.queries = []
        self.last_trace = {"mode": "test", "hits": []}
        self.store = SimpleNamespace(_identifiers=self.identifiers)

    @staticmethod
    def identifiers(text):
        return ["gs105"] if "GS105" in text or "gs105" in text.lower() else []

    def retrieve(self, query, top_k):
        self.queries.append(query)
        return [
            KnowledgeHit(
                "GS105 offline troubleshooting evidence.",
                "base.md",
                "GS105",
                0.9,
                {"identifiers": ["gs105"]},
            )
        ]


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
        query = agent.retriever.queries[0]
        self.assertIn("GS105", query.keywords)
        self.assertIn("port indicator off", query.keywords)
        self.assertIn(
            "Visual observation",
            agent.ai.generate_reply_async.call_args.kwargs["multimodal_context"],
        )

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


if __name__ == "__main__":
    unittest.main()
