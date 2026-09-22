"""Lease and execute durable mail jobs from the local raw-message spool."""
import asyncio
import hashlib
import uuid
from dataclasses import dataclass

from email_agent.domain.mail_jobs import JobStatus


@dataclass(frozen=True)
class JobRunResult:
    job_id: str
    status: JobStatus
    error: str = ""


class MailJobWorker:
    def __init__(self, store, spool, *, parse_raw_email, batch_size: int = 20,
                 max_concurrent: int = 3, max_attempts: int = 3,
                 retry_delays=None, lease_seconds: int = 600,
                 worker_id: str = ""):
        self.store = store
        self.spool = spool
        self.parse_raw_email = parse_raw_email
        self.batch_size = max(1, int(batch_size))
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_attempts = max(1, int(max_attempts))
        self.retry_delays = tuple(retry_delays or (60, 300, 1800))
        self.lease_seconds = max(1, int(lease_seconds))
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"

    async def run_available(self, handler) -> list[JobRunResult]:
        claimed = []
        for _ in range(self.batch_size):
            job = self.store.claim_next(self.worker_id, self.lease_seconds)
            if not job:
                break
            claimed.append(dict(job))

        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def execute(job):
            async with semaphore:
                try:
                    raw = self.spool.read(job["raw_path"])
                    if hashlib.sha256(raw).hexdigest() != job["raw_sha256"]:
                        raise ValueError("原始邮件校验失败")
                    message = self.parse_raw_email(raw)
                    message.imap_uid = str(job["imap_uid"])
                    target = await handler(message, job["id"])
                    current = self.store.get_job(job["id"])["status"]
                    if current == JobStatus.PROCESSING.value:
                        self.store.complete_job(job["id"], target, actor=self.worker_id)
                    final = self.store.get_job(job["id"])["status"]
                    return JobRunResult(job["id"], JobStatus(final))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    current = self.store.get_job(job["id"])["status"]
                    if current == JobStatus.AWAITING_CONFIRMATION.value:
                        return JobRunResult(
                            job["id"], JobStatus.AWAITING_CONFIRMATION, str(exc)
                        )
                    status = self.store.fail_job(
                        job["id"], str(exc), self.max_attempts,
                        self.retry_delays, actor=self.worker_id,
                    )
                    return JobRunResult(job["id"], status, str(exc))

        if not claimed:
            return []
        return list(await asyncio.gather(*(execute(job) for job in claimed)))
