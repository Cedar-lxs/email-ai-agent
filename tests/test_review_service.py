import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.delivery_service import (
    DeliveryService,
    DeliveryUncertainError,
)
from email_agent.application.review_service import ReviewService
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_job_store import MailJobStore
from email_agent.infrastructure.mail_sender import (
    MailSender,
    SmtpDeliveryResult,
    SmtpOutcome,
)


class FakeSender(MailSender):
    def __init__(self):
        super().__init__("test", 465, "agent@test", "secret")
        self.result = SmtpDeliveryResult(SmtpOutcome.ACCEPTED)
        self.calls = 0

    def deliver_message(self, message, timeout, before_send):
        self.calls += 1
        if self.result.outcome != SmtpOutcome.SAFE_FAILURE:
            before_send()
        return self.result


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = EmailDB(str(root / "test.db"))
        self.store = MailJobStore(self.db)
        self.sender = FakeSender()
        self.delivery = DeliveryService(self.store, self.sender)
        self.service = ReviewService(self.db, self.delivery, root / "drafts")
        self.db.mark_processed("m1", "主题", "a@test", status="draft_ready", original_body="问题")
        path = self.sender.save_draft("a@test", "主题", "草稿", str(root / "drafts"), "m1")
        self.db.update_status("m1", "draft_ready", "草稿", path)

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_edit_approve_and_delete(self):
        self.service.edit("m1", "人工修改")
        self.assertEqual(self.db.get_email("m1")["draft_text"], "人工修改")
        self.service.approve("m1")
        self.assertEqual(self.db.get_email("m1")["status"], "replied")
        self.assertEqual(self.service.delete(["m1"]), ["m1"])

    def test_send_failure_keeps_draft(self):
        self.sender.result = SmtpDeliveryResult(SmtpOutcome.SAFE_FAILURE, "offline")
        with self.assertRaises(RuntimeError):
            self.service.approve("m1")
        self.assertEqual(self.db.get_email("m1")["status"], "draft_ready")
        jobs = self.store.find_jobs_by_message_id("m1")
        self.assertEqual(jobs[0]["status"], "draft_ready")

    def test_approval_reuses_the_existing_draft_job(self):
        self.store.record_discovery(
            job_id="job-draft", account="agent@test", folder="INBOX",
            uid_validity="7", imap_uid=7, message_id="m1",
            raw_path="mail.eml", raw_sha256="hash",
        )
        self.store.claim_next("worker", lease_seconds=60)
        self.store.complete_job("job-draft", "draft_ready", actor="worker")

        self.service.approve("m1", actor="admin")

        jobs = self.store.find_jobs_by_message_id("m1")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "job-draft")
        self.assertEqual(jobs[0]["status"], "sent")

    def test_uncertain_send_blocks_second_approval(self):
        self.sender.result = SmtpDeliveryResult(SmtpOutcome.UNCERTAIN, "timeout")
        with self.assertRaises(DeliveryUncertainError):
            self.service.approve("m1", actor="admin")
        jobs = self.store.find_jobs_by_message_id("m1")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "awaiting_confirmation")
        self.assertEqual(self.store.get_operation_counts()["awaiting_confirmation"], 1)
        with self.assertRaisesRegex(ValueError, "待确认"):
            self.service.approve("m1", actor="admin")
        self.assertEqual(self.sender.calls, 1)


if __name__ == "__main__":
    unittest.main()
