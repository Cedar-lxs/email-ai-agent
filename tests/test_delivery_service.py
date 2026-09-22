import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.delivery_service import DeliveryService
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_job_store import MailJobStore
from email_agent.infrastructure.mail_sender import (
    MailSender,
    SmtpDeliveryResult,
    SmtpOutcome,
)


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 9, 22, 11, 0, 0)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class DeliveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = EmailDB(str(Path(self.temp.name) / "emails.db"))
        self.clock = FakeClock()
        self.store = MailJobStore(self.db, clock=self.clock)
        self.sender = Mock(spec=MailSender)
        self.sender.account = "support@example.com"
        self.sender.build_reply_message.side_effect = lambda *args: {"args": args}
        self.service = DeliveryService(
            self.store, self.sender, smtp_timeout=30, send_lease_seconds=60
        )

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def _job(self, label):
        job_id = f"job-{label}"
        message_id = f"{label}@example.com"
        self.store.record_discovery(
            job_id=job_id, account="support@example.com", folder="INBOX",
            uid_validity="7", imap_uid=len(label) + ord(label[0]),
            message_id=message_id, raw_path=f"{job_id}.eml", raw_sha256="hash",
        )
        self.store.claim_next("worker", lease_seconds=60)
        self.db.mark_processed(
            message_id, f"Question {label}", "customer@example.com",
            status="pending", original_body="question",
        )
        return job_id, message_id

    def _prepared(self, label):
        job_id, message_id = self._job(label)
        return self.service.prepare_reply(
            job_id=job_id,
            business_message_id=message_id,
            recipient="customer@example.com",
            subject=f"Question {label}",
            body=f"Answer {label}",
            in_reply_to=message_id,
            created_by="auto",
        )

    @staticmethod
    def _after_start(outcome, detail=""):
        def deliver(message, timeout, before_send):
            before_send()
            return SmtpDeliveryResult(outcome, detail)
        return deliver

    def test_prepare_persists_fixed_message_id_before_smtp(self):
        job_id, message_id = self._job("prepare")
        delivery_id = self.service.prepare_reply(
            job_id=job_id, business_message_id=message_id,
            recipient="customer@example.com", subject="Question", body="Answer",
            in_reply_to=message_id, created_by="auto",
        )
        row = self.store.get_delivery(delivery_id)
        self.assertEqual(row["status"], "prepared")
        self.assertRegex(row["email_message_id"], r"^<ea-[0-9a-f]+@example\.com>$")
        self.sender.deliver_message.assert_not_called()

    def test_pre_send_failure_is_retryable_but_send_exception_is_uncertain(self):
        safe_id = self._prepared("safe")
        self.sender.deliver_message.return_value = SmtpDeliveryResult(
            SmtpOutcome.SAFE_FAILURE, "login"
        )
        result = self.service.send_prepared(safe_id)
        self.assertEqual(result.outcome, SmtpOutcome.SAFE_FAILURE)
        self.assertEqual(self.store.get_delivery(safe_id)["status"], "failed_safe")

        uncertain_id = self._prepared("uncertain")
        self.sender.deliver_message.reset_mock()
        self.sender.deliver_message.side_effect = self._after_start(
            SmtpOutcome.UNCERTAIN, "timeout"
        )
        result = self.service.send_prepared(uncertain_id)
        self.assertEqual(result.outcome, SmtpOutcome.UNCERTAIN)
        self.assertEqual(self.store.get_delivery(uncertain_id)["status"], "uncertain")
        self.assertEqual(
            self.store.get_job("job-uncertain")["status"], "awaiting_confirmation"
        )

    def test_stale_sending_is_never_called_again_automatically(self):
        delivery_id = self._prepared("crash")
        self.store.mark_delivery_sending(delivery_id, lease_seconds=1)
        self.clock.advance(seconds=2)
        self.store.recover_expired()
        self.sender.reset_mock()
        with self.assertRaisesRegex(ValueError, "人工确认"):
            self.service.send_prepared(delivery_id)
        self.sender.deliver_message.assert_not_called()

    def test_accepted_delivery_updates_business_job_and_conversation_atomically(self):
        delivery_id = self._prepared("accepted")
        self.sender.deliver_message.side_effect = self._after_start(SmtpOutcome.ACCEPTED)
        result = self.service.send_prepared(delivery_id)
        self.assertEqual(result.outcome, SmtpOutcome.ACCEPTED)
        self.assertEqual(self.store.get_delivery(delivery_id)["status"], "accepted")
        self.assertEqual(self.store.get_job("job-accepted")["status"], "sent")
        mail = self.db.get_email("accepted@example.com")
        self.assertEqual(mail["status"], "replied")
        history = self.db.get_history_for_sender("customer@example.com")
        self.assertEqual([row["content"] for row in history], ["Answer accepted"])

    def test_accepted_delivery_blocks_a_second_automatic_delivery_for_same_message(self):
        first_id = self._prepared("duplicate")
        self.sender.deliver_message.side_effect = self._after_start(SmtpOutcome.ACCEPTED)
        self.service.send_prepared(first_id)

        second_job_id = "job-duplicate-copy"
        self.store.record_discovery(
            job_id=second_job_id, account="support@example.com", folder="INBOX",
            uid_validity="8", imap_uid=999,
            message_id="duplicate@example.com", raw_path="copy.eml", raw_sha256="hash",
        )
        self.store.claim_next("worker-2", lease_seconds=60)
        with self.assertRaisesRegex(ValueError, "已经发送"):
            self.service.prepare_reply(
                job_id=second_job_id,
                business_message_id="duplicate@example.com",
                recipient="customer@example.com",
                subject="Question duplicate",
                body="Answer duplicate",
                in_reply_to="duplicate@example.com",
                created_by="auto",
            )

    def test_confirm_sent_does_not_call_smtp(self):
        delivery_id = self._prepared("confirm")
        self.sender.deliver_message.side_effect = self._after_start(
            SmtpOutcome.UNCERTAIN, "timeout"
        )
        self.service.send_prepared(delivery_id)
        self.sender.reset_mock()
        self.service.confirm_sent(
            delivery_id, actor="admin", reason="已在已发送目录中核对"
        )
        self.sender.deliver_message.assert_not_called()
        self.assertEqual(self.store.get_delivery(delivery_id)["status"], "accepted")
        self.assertEqual(self.store.get_job("job-confirm")["status"], "sent")

    def test_authorized_resend_requires_reason_and_supersedes_uncertain_delivery(self):
        delivery_id = self._prepared("resend")
        self.sender.deliver_message.side_effect = self._after_start(
            SmtpOutcome.UNCERTAIN, "timeout"
        )
        self.service.send_prepared(delivery_id)
        with self.assertRaisesRegex(ValueError, "原因"):
            self.service.authorize_resend(delivery_id, actor="admin", reason="")

        self.sender.deliver_message.side_effect = self._after_start(SmtpOutcome.ACCEPTED)
        replacement_id, result = self.service.authorize_resend(
            delivery_id, actor="admin", reason="服务商确认原邮件未接收"
        )
        replacement = self.store.get_delivery(replacement_id)
        self.assertEqual(result.outcome, SmtpOutcome.ACCEPTED)
        self.assertEqual(replacement["supersedes_id"], delivery_id)
        self.assertEqual(replacement["created_by"], "admin")
        self.assertNotEqual(
            replacement["email_message_id"],
            self.store.get_delivery(delivery_id)["email_message_id"],
        )

    def test_authorized_resend_safe_failure_moves_job_to_visible_dead_letter(self):
        delivery_id = self._prepared("resend-safe")
        self.sender.deliver_message.side_effect = self._after_start(
            SmtpOutcome.UNCERTAIN, "timeout"
        )
        self.service.send_prepared(delivery_id)
        self.sender.deliver_message.side_effect = None
        self.sender.deliver_message.return_value = SmtpDeliveryResult(
            SmtpOutcome.SAFE_FAILURE, "login"
        )

        replacement_id, result = self.service.authorize_resend(
            delivery_id, actor="admin", reason="服务商确认原邮件未接收"
        )

        self.assertEqual(result.outcome, SmtpOutcome.SAFE_FAILURE)
        self.assertEqual(self.store.get_delivery(replacement_id)["status"], "failed_safe")
        self.assertEqual(self.store.get_job("job-resend-safe")["status"], "dead_letter")
        self.assertEqual(self.store.get_operation_counts()["dead_letter"], 1)


class MailSenderTransportTests(unittest.TestCase):
    def setUp(self):
        self.sender = MailSender(
            "smtp.example.com", 465, "support@example.com", "secret"
        )

    def test_build_reply_message_sets_persisted_id_and_normalizes_terms(self):
        message = self.sender.build_reply_message(
            "customer@example.com", "Question", "Use the WeChat Mini Program.",
            "inbound@example.com", "<fixed@example.com>",
        )
        self.assertEqual(message["Message-ID"], "<fixed@example.com>")
        body = message.get_payload(decode=True).decode(message.get_content_charset())
        self.assertIn("Amitres APP", body)

    def test_connect_failure_is_safe_and_does_not_cross_send_boundary(self):
        boundary = Mock()
        with patch(
            "email_agent.infrastructure.mail_sender.smtplib.SMTP_SSL",
            side_effect=OSError("offline"),
        ):
            result = self.sender.deliver_message(Mock(), 30, boundary)
        self.assertEqual(result.outcome, SmtpOutcome.SAFE_FAILURE)
        boundary.assert_not_called()

    def test_send_failure_after_boundary_is_uncertain(self):
        boundary = Mock()
        smtp = Mock()
        smtp.send_message.side_effect = TimeoutError("timeout")
        with patch(
            "email_agent.infrastructure.mail_sender.smtplib.SMTP_SSL",
            return_value=smtp,
        ):
            result = self.sender.deliver_message(Mock(), 30, boundary)
        self.assertEqual(result.outcome, SmtpOutcome.UNCERTAIN)
        boundary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
