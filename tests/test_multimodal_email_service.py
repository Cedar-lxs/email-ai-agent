import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.email_service import EmailAgent
from email_agent.domain.models import (
    EmailAttachment,
    EmailMedia,
    IntentResult,
    KnowledgeHit,
    MultimodalObservation,
    ParsedEmail,
    StoredMedia,
)
from email_agent.infrastructure.attachment_storage import StoredAttachment
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_sender import SmtpDeliveryResult, SmtpOutcome
from email_agent.infrastructure.product_index import ProductRecord


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

    def email_with_attachment(self):
        email = self.email()
        email.media = []
        email.attachments = [
            EmailAttachment(
                "attachment-1", "diagnosis.txt", "text/plain", "attachment",
                size_bytes=34, data=b"serial GS105\nlink state disconnected",
            )
        ]
        return email

    def attach_storage_result(self, agent, stored, errors=None):
        agent.attachment_storage = SimpleNamespace(
            save=Mock(return_value=(stored, errors or [])),
            manifest=Mock(return_value=[{
                "attachment_id": item.attachment_id,
                "filename": item.filename,
                "content_type": item.content_type,
                "source": item.source,
                "path": item.path,
                "size_bytes": item.size_bytes,
                "extracted_text": item.extracted_text,
                "extraction_status": item.extraction_status,
                "extraction_error": item.extraction_error,
                "metadata": item.metadata,
            } for item in stored]),
        )

    def readable_attachment(self):
        return StoredAttachment(
            "attachment-1", "diagnosis.txt", "text/plain", "attachment",
            str(self.temp_root / "diagnosis.txt"), 34,
            extracted_text="serial GS105\nlink state disconnected",
            extraction_status="extracted",
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
        agent.delivery = SimpleNamespace(
            prepare_reply=Mock(return_value="delivery-1"),
            send_prepared=Mock(return_value=SmtpDeliveryResult(SmtpOutcome.ACCEPTED)),
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

    async def test_low_risk_full_auto_email_with_readable_attachment_sends_reply(self):
        agent = self.agent(MultimodalObservation(), mode="full_auto")
        self.attach_storage_result(agent, [self.readable_attachment()])

        completed = await agent._process_email_async(self.email_with_attachment())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "replied")
        self.assertEqual(
            self.db.parse_attachment_manifest(row)[0]["extraction_status"], "extracted",
        )
        agent.delivery.prepare_reply.assert_called_once()
        agent.delivery.send_prepared.assert_called_once_with("delivery-1")
        agent.sender.send_reply.assert_not_called()

    async def test_attachment_extraction_error_creates_draft_instead_of_auto_sending(self):
        agent = self.agent(MultimodalObservation(), mode="full_auto")
        broken = StoredAttachment(
            "attachment-1", "broken.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "attachment", str(self.temp_root / "broken.docx"), 12,
            extraction_status="extraction_failed", extraction_error="invalid docx",
        )
        self.attach_storage_result(agent, [broken])

        completed = await agent._process_email_async(self.email_with_attachment())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "draft_ready")
        self.assertIn("attachment_processing_error", self.db.parse_decision_trace(row)["blocking_reasons"])
        agent.sender.send_reply.assert_not_called()

    async def test_retrieval_query_includes_extracted_attachment_text(self):
        agent = self.agent(MultimodalObservation())
        self.attach_storage_result(agent, [self.readable_attachment()])

        await agent._process_email_async(self.email_with_attachment())

        query = agent.retriever.queries[0]
        self.assertIn("link state disconnected", query.text)
        self.assertIn("diagnosis", query.keywords)

    async def test_reply_generation_receives_attachment_context(self):
        agent = self.agent(MultimodalObservation())
        self.attach_storage_result(agent, [self.readable_attachment()])

        await agent._process_email_async(self.email_with_attachment())

        attachment_context = agent.ai.generate_reply_async.call_args.kwargs["attachment_context"]
        self.assertIn("diagnosis.txt", attachment_context)
        self.assertIn("link state disconnected", attachment_context)

    async def test_web_search_query_excludes_attachment_extracted_text(self):
        agent = self.agent(MultimodalObservation(), mode="full_auto")
        agent.retriever = EmptyRetriever()
        agent.kb = agent.retriever
        sensitive = StoredAttachment(
            "attachment-1", "diagnosis.txt", "text/plain", "attachment",
            str(self.temp_root / "diagnosis.txt"), 64,
            extracted_text="secret_token_ABC123 link state disconnected",
            extraction_status="extracted",
        )
        self.attach_storage_result(agent, [sensitive])
        agent.web_search = SimpleNamespace(
            available=True,
            search=Mock(return_value=[
                SimpleNamespace(
                    title="Switch troubleshooting",
                    snippet="Check Ethernet cable and link light.",
                    url="https://example.com/switch-guide",
                )
            ]),
        )
        agent.web_search_config = {
            "allowed_intents": ["网络连接"],
            "auto_send_low_risk": True,
        }

        await agent._process_email_async(self.email_with_attachment())

        local_query = agent.retriever.queries[0]
        web_query = agent.web_search.search.call_args.args[0]
        self.assertIn("secret_token_ABC123", local_query.text)
        self.assertNotIn("secret_token_ABC123", web_query)
        self.assertNotIn("link state disconnected", web_query)
        self.assertIn("diagnosis", web_query)

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
        agent.delivery.prepare_reply.assert_called_once()
        agent.delivery.send_prepared.assert_called_once_with("delivery-1")
        agent.sender.send_reply.assert_not_called()

    async def test_product_management_conflict_blocks_full_auto_send_and_records_reason(self):
        observation = MultimodalObservation(
            product_identifiers=["GS105"],
            model_numbers=["GS105"],
            switch_management_type="unmanaged",
            confidence=0.92,
        )
        agent = self.agent(observation, mode="full_auto")
        agent.product_index = SimpleNamespace(find=Mock(return_value=[
            ProductRecord(
                model="GS105", management_type="managed",
                ports=["5 Gigabit RJ45"], source="products.vector.json",
            )
        ]))

        completed = await agent._process_email_async(self.email())

        self.assertTrue(completed)
        row = self.db.get_email("m1")
        self.assertEqual(row["status"], "draft_ready")
        self.assertIn("product_conflict", self.db.parse_decision_trace(row)["blocking_reasons"])
        agent.sender.send_reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
