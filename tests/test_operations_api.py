import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.delivery_service import DeliveryService
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_job_store import MailJobStore
from email_agent.infrastructure.mail_sender import (
    MailSender,
    SmtpDeliveryResult,
    SmtpOutcome,
)
from email_agent.web.app import create_app


class OperationsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = EmailDB(str(Path(self.temp.name) / "operations.db"))
        self.store = MailJobStore(self.db)
        self.sender = Mock(spec=MailSender)
        self.sender.account = "support@example.com"
        self.sender.build_reply_message.side_effect = lambda *args: {"args": args}
        self.smtp_outcome = SmtpOutcome.ACCEPTED

        def deliver(_message, timeout, before_send):
            del timeout
            if self.smtp_outcome != SmtpOutcome.SAFE_FAILURE:
                before_send()
            return SmtpDeliveryResult(self.smtp_outcome, "test outcome")

        self.sender.deliver_message.side_effect = deliver
        self.delivery = DeliveryService(self.store, self.sender)
        self.agent = SimpleNamespace(
            db=self.db,
            mail_jobs=self.store,
            delivery=self.delivery,
            config={"workflow": {"mode": "semi_auto"}},
        )
        self.app = create_app(self.agent, object(), object(), True)
        self.client = self.app.test_client()
        setup = self.client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "password": "SecureAdmin!2026",
                "confirm_password": "SecureAdmin!2026",
            },
        )
        self.assertEqual(setup.status_code, 201)
        self.auth_headers = {"X-CSRF-Token": setup.get_json()["csrf_token"]}
        self.uid = 0

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def _job(self, label):
        self.uid += 1
        job_id = f"job-{label}"
        message_id = f"{label}@example.com"
        self.store.record_discovery(
            job_id=job_id,
            account="support@example.com",
            folder="INBOX",
            uid_validity="7",
            imap_uid=self.uid,
            message_id=message_id,
            raw_path=f"{job_id}.eml",
            raw_sha256="hash",
        )
        return job_id, message_id

    def _dead_letter(self, label="dead"):
        job_id, _ = self._job(label)
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE mail_jobs SET status='processing', attempt_count=1 WHERE id=?",
                (job_id,),
            )
        self.store.fail_job(job_id, "failed", max_attempts=1, retry_delays=[60])
        return job_id

    def _retry_wait(self, label="retry"):
        job_id, _ = self._job(label)
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE mail_jobs SET status='processing', attempt_count=1 WHERE id=?",
                (job_id,),
            )
        self.store.fail_job(job_id, "temporary", max_attempts=3, retry_delays=[60])
        return job_id

    def _uncertain_delivery(self, label="uncertain"):
        job_id, message_id = self._job(label)
        self.store.claim_next("test", 60)
        self.db.mark_processed(
            message_id, "Question", "customer@example.com",
            status="draft_ready", original_body="question",
        )
        delivery_id = self.delivery.prepare_reply(
            job_id=job_id,
            business_message_id=message_id,
            recipient="customer@example.com",
            subject="Question",
            body="Answer",
            in_reply_to=message_id,
            created_by="auto",
        )
        self.smtp_outcome = SmtpOutcome.UNCERTAIN
        self.delivery.send_prepared(delivery_id)
        self.sender.deliver_message.reset_mock()
        return delivery_id, job_id

    def test_operations_list_requires_login_and_filters_requested_states(self):
        self._dead_letter()
        self._retry_wait()
        self._uncertain_delivery()
        anonymous = self.app.test_client()
        self.assertEqual(
            anonymous.get("/api/operations/mail-jobs").status_code, 401
        )

        response = self.client.get(
            "/api/operations/mail-jobs?status=dead_letter,awaiting_confirmation",
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            {item["status"] for item in payload["jobs"]},
            {"dead_letter", "awaiting_confirmation"},
        )

    def test_confirm_sent_requires_csrf_and_reason_and_never_calls_smtp(self):
        delivery_id, _ = self._uncertain_delivery()
        self.assertEqual(
            self.client.post(
                f"/api/operations/deliveries/{delivery_id}/confirm-sent",
                json={"reason": "已核对发送记录"},
            ).status_code,
            403,
        )
        missing_reason = self.client.post(
            f"/api/operations/deliveries/{delivery_id}/confirm-sent",
            json={},
            headers=self.auth_headers,
        )
        self.assertEqual(missing_reason.status_code, 400)

        confirmed = self.client.post(
            f"/api/operations/deliveries/{delivery_id}/confirm-sent",
            json={"reason": "已在邮箱已发送目录核对"},
            headers=self.auth_headers,
        )

        self.assertEqual(confirmed.status_code, 200)
        self.sender.deliver_message.assert_not_called()

    def test_authorized_resend_records_admin_reason_and_new_delivery(self):
        original_id, _ = self._uncertain_delivery()
        self.smtp_outcome = SmtpOutcome.ACCEPTED

        response = self.client.post(
            f"/api/operations/deliveries/{original_id}/authorize-resend",
            json={"reason": "邮件服务商确认原邮件未接收"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        replacement = self.store.get_delivery(response.get_json()["delivery_id"])
        self.assertEqual(replacement["supersedes_id"], original_id)
        self.assertEqual(replacement["created_by"], "admin")

    def test_list_validation_page_cap_and_detail_event_ordering(self):
        job_id = self._dead_letter()
        invalid = self.client.get(
            "/api/operations/mail-jobs?status=sent", headers=self.auth_headers
        )
        self.assertEqual(invalid.status_code, 400)
        listed = self.client.get(
            "/api/operations/mail-jobs?status=dead_letter&page_size=999",
            headers=self.auth_headers,
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["page_size"], 100)

        detail = self.client.get(
            f"/api/operations/mail-jobs/{job_id}", headers=self.auth_headers
        )
        self.assertEqual(detail.status_code, 200)
        event_ids = [event["id"] for event in detail.get_json()["events"]]
        self.assertEqual(event_ids, sorted(event_ids))

    def test_retry_and_escalation_enforce_states_and_record_actor_reason(self):
        retry_id = self._retry_wait()
        wrong_retry = self.client.post(
            f"/api/operations/mail-jobs/{retry_id}/retry",
            json={"reason": "立即重试"}, headers=self.auth_headers,
        )
        self.assertEqual(wrong_retry.status_code, 409)

        dead_id = self._dead_letter("dead-retry")
        retried = self.client.post(
            f"/api/operations/mail-jobs/{dead_id}/retry",
            json={"reason": "已修复配置"}, headers=self.auth_headers,
        )
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(self.store.get_job(dead_id)["status"], "pending")

        terminal_id, _ = self._job("terminal")
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE mail_jobs SET status='draft_ready' WHERE id=?", (terminal_id,)
            )
        refused = self.client.post(
            f"/api/operations/mail-jobs/{terminal_id}/escalate",
            json={"reason": "转人工检查"}, headers=self.auth_headers,
        )
        self.assertEqual(refused.status_code, 409)

        attention_id = self._retry_wait("attention")
        escalated = self.client.post(
            f"/api/operations/mail-jobs/{attention_id}/escalate",
            json={"reason": "需要人工诊断"}, headers=self.auth_headers,
        )
        self.assertEqual(escalated.status_code, 200)
        events = self.store.get_events(attention_id)
        self.assertEqual(events[-1]["actor"], "admin")
        self.assertEqual(events[-1]["reason"], "需要人工诊断")

    def test_mail_stats_include_operation_attention_counts(self):
        self._dead_letter()
        self._retry_wait()
        self._uncertain_delivery()

        response = self.client.get("/api/mails/stats", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        operations = response.get_json()["operations"]
        self.assertEqual(operations["retry_wait"], 1)
        self.assertEqual(operations["dead_letter"], 1)
        self.assertEqual(operations["awaiting_confirmation"], 1)
        self.assertEqual(operations["total_attention"], 3)
        self.assertTrue(operations["oldest_attention_at"])


if __name__ == "__main__":
    unittest.main()
