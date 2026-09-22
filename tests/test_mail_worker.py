import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.mail_worker import MailJobWorker
from email_agent.domain.mail_jobs import JobStatus
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_fetcher import MailFetcher
from email_agent.infrastructure.mail_job_store import MailJobStore
from email_agent.infrastructure.raw_mail_spool import RawMailSpool


RAW_EMAIL = (
    b"From: Customer <customer@example.com>\r\n"
    b"Subject: Port down\r\n"
    b"Message-ID: <m1@example.com>\r\n"
    b"Date: Tue, 22 Sep 2026 10:00:00 +0800\r\n\r\n"
    b"Port 1 is down."
)


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 9, 22, 12, 0, 0)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class MailWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = EmailDB(str(root / "emails.db"))
        self.clock = FakeClock()
        self.store = MailJobStore(self.db, clock=self.clock)
        self.spool = RawMailSpool(root / "spool", clock=self.clock)
        parser = MailFetcher(
            "imap.example.com", 993, "support@example.com", "secret"
        ).parse_raw_email
        self.worker = MailJobWorker(
            self.store, self.spool, parse_raw_email=parser,
            batch_size=10, max_concurrent=2, max_attempts=3,
            retry_delays=[60, 300, 1800], lease_seconds=10,
            worker_id="test-worker",
        )
        self.next_uid = 1

    async def asyncTearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def _discover_raw(self, job_id, raw=RAW_EMAIL):
        stored = self.spool.store(job_id, raw)
        uid = self.next_uid
        self.next_uid += 1
        self.store.record_discovery(
            job_id=job_id, account="support@example.com", folder="INBOX",
            uid_validity="7", imap_uid=uid,
            message_id=f"m-{uid}@example.com", raw_path=stored.path,
            raw_sha256=stored.sha256,
        )
        return stored

    async def test_worker_replays_spooled_mail_without_imap(self):
        self._discover_raw("job-1")
        handler = AsyncMock(return_value=JobStatus.DRAFT_READY)
        results = await self.worker.run_available(handler)
        handler.assert_awaited_once()
        parsed, job_id = handler.await_args.args
        self.assertEqual(parsed.message_id, "m1@example.com")
        self.assertEqual(job_id, "job-1")
        self.assertEqual(results[0].status, JobStatus.DRAFT_READY)

    async def test_transient_handler_failure_retries_then_dead_letters(self):
        self._discover_raw("job-1")
        handler = AsyncMock(side_effect=RuntimeError("AI timeout"))
        await self.worker.run_available(handler)
        self.assertEqual(self.store.get_job("job-1")["status"], "retry_wait")
        self.clock.advance(seconds=60)
        await self.worker.run_available(handler)
        self.clock.advance(seconds=300)
        await self.worker.run_available(handler)
        self.assertEqual(self.store.get_job("job-1")["status"], "dead_letter")
        self.assertEqual(handler.await_count, 3)

    async def test_worker_preserves_awaiting_confirmation_on_handler_error(self):
        self._discover_raw("job-1")

        async def uncertain_handler(message, job_id):
            delivery_id = self.store.create_delivery(
                job_id=job_id, business_message_id="m1@example.com",
                email_message_id="<delivery@example.com>",
                recipient="customer@example.com", subject="Re: port",
                body="answer", body_sha256="hash", created_by="auto",
            )
            self.store.mark_delivery_sending(delivery_id, lease_seconds=10)
            self.store.mark_delivery_uncertain(delivery_id, "timeout")
            raise RuntimeError("delivery uncertain")

        results = await self.worker.run_available(uncertain_handler)
        self.assertEqual(
            self.store.get_job("job-1")["status"], "awaiting_confirmation"
        )
        self.assertEqual(results[0].status, JobStatus.AWAITING_CONFIRMATION)

    async def test_missing_and_tampered_spool_files_are_retryable_failures(self):
        missing = self._discover_raw("missing")
        Path(missing.path).unlink()
        tampered = self._discover_raw("tampered")
        Path(tampered.path).write_bytes(b"changed")
        handler = AsyncMock(return_value=JobStatus.DRAFT_READY)
        results = await self.worker.run_available(handler)
        self.assertEqual([result.status for result in results], [
            JobStatus.RETRY_WAIT, JobStatus.RETRY_WAIT,
        ])
        self.assertIn("No such file", results[0].error)
        self.assertIn("校验失败", results[1].error)
        handler.assert_not_awaited()

    async def test_worker_respects_max_concurrent(self):
        for index in range(4):
            self._discover_raw(f"job-{index}")
        active = 0
        peak = 0

        async def handler(message, job_id):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return JobStatus.DRAFT_READY

        await self.worker.run_available(handler)
        self.assertEqual(peak, 2)

    async def test_cancelled_worker_leaves_lease_for_recovery(self):
        self._discover_raw("job-1")
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(message, job_id):
            started.set()
            await release.wait()
            return JobStatus.DRAFT_READY

        task = asyncio.create_task(self.worker.run_available(handler))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.store.get_job("job-1")["status"], "processing")
        self.clock.advance(seconds=11)
        self.store.recover_expired()
        self.assertEqual(self.store.get_job("job-1")["status"], "retry_wait")

    async def test_terminal_jobs_are_not_claimed_again(self):
        self._discover_raw("job-1")
        self.store.claim_next("setup", lease_seconds=10)
        self.store.complete_job("job-1", JobStatus.DRAFT_READY, actor="setup")
        handler = AsyncMock(return_value=JobStatus.DRAFT_READY)
        self.assertEqual(await self.worker.run_available(handler), [])
        handler.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
