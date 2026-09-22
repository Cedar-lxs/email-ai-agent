"""Transactional SQLite repository for durable mail processing."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

from email_agent.domain.mail_jobs import (
    DELIVERY_TRANSITIONS,
    JOB_TRANSITIONS,
    DeliveryStatus,
    JobStatus,
    MailboxCursor,
    require_transition,
)


class MailJobStore:
    def __init__(self, db, clock=None):
        self.db = db
        self.clock = clock or datetime.now
        self._init_schema()

    @staticmethod
    def timestamp(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")

    def _now(self) -> str:
        return self.timestamp(self.clock())

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mailbox_cursors (
                    account TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    uid_validity TEXT NOT NULL,
                    last_scanned_uid INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account, folder)
                );
                CREATE TABLE IF NOT EXISTS mail_jobs (
                    id TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    uid_validity TEXT NOT NULL,
                    imap_uid INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    raw_path TEXT NOT NULL,
                    raw_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_error_code TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    seen_at TEXT,
                    seen_result TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE (account, folder, uid_validity, imap_uid)
                );
                CREATE INDEX IF NOT EXISTS ix_mail_jobs_status_due
                    ON mail_jobs(status, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS ix_mail_jobs_message_id
                    ON mail_jobs(message_id);
                CREATE TABLE IF NOT EXISTS outbound_deliveries (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    business_message_id TEXT NOT NULL,
                    email_message_id TEXT NOT NULL UNIQUE,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    in_reply_to TEXT NOT NULL DEFAULT '',
                    body_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    smtp_response TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    supersedes_id TEXT,
                    lease_expires_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES mail_jobs(id),
                    FOREIGN KEY(supersedes_id) REFERENCES outbound_deliveries(id)
                );
                CREATE INDEX IF NOT EXISTS ix_deliveries_job
                    ON outbound_deliveries(job_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_business_delivery
                    ON outbound_deliveries(business_message_id)
                    WHERE status IN ('prepared', 'sending', 'uncertain');
                CREATE TABLE IF NOT EXISTS mail_job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    delivery_id TEXT,
                    event_type TEXT NOT NULL,
                    from_status TEXT NOT NULL DEFAULT '',
                    to_status TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES mail_jobs(id),
                    FOREIGN KEY(delivery_id) REFERENCES outbound_deliveries(id)
                );
                CREATE INDEX IF NOT EXISTS ix_job_events_job
                    ON mail_job_events(job_id, id);
            """)

    def _append_event(self, conn, job_id, event_type, from_status="", to_status="",
                      actor="", reason="", delivery_id=None, metadata=None):
        conn.execute("""
            INSERT INTO mail_job_events
                (job_id, delivery_id, event_type, from_status, to_status,
                 actor, reason, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, delivery_id, event_type, str(from_status or ""), str(to_status or ""),
            actor or "", str(reason or "")[:1000],
            json.dumps(metadata or {}, ensure_ascii=False), self._now(),
        ))

    def get_cursor(self, account: str, folder: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_cursors WHERE account=? AND folder=?",
                (account, folder),
            ).fetchone()
        if not row:
            return None
        return MailboxCursor(row["account"], row["folder"], row["uid_validity"],
                             int(row["last_scanned_uid"]))

    def reset_cursor(self, account: str, folder: str, uid_validity: str):
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT INTO mailbox_cursors
                    (account, folder, uid_validity, last_scanned_uid, updated_at)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(account, folder) DO UPDATE SET
                    uid_validity=excluded.uid_validity,
                    last_scanned_uid=0,
                    updated_at=excluded.updated_at
            """, (account, folder, uid_validity, now))

    def advance_cursor(self, account: str, folder: str, uid_validity: str, uid: int):
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._advance_cursor(conn, account, folder, uid_validity, uid, now)

    @staticmethod
    def _advance_cursor(conn, account, folder, uid_validity, uid, now):
        conn.execute("""
            INSERT INTO mailbox_cursors
                (account, folder, uid_validity, last_scanned_uid, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account, folder) DO UPDATE SET
                uid_validity=excluded.uid_validity,
                last_scanned_uid=CASE
                    WHEN mailbox_cursors.uid_validity=excluded.uid_validity
                    THEN MAX(mailbox_cursors.last_scanned_uid, excluded.last_scanned_uid)
                    ELSE excluded.last_scanned_uid
                END,
                updated_at=excluded.updated_at
        """, (account, folder, uid_validity, int(uid), now))

    def record_discovery(self, *, job_id, account, folder, uid_validity, imap_uid,
                         message_id, raw_path, raw_sha256):
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("""
                SELECT * FROM mail_jobs
                WHERE account=? AND folder=? AND uid_validity=? AND imap_uid=?
            """, (account, folder, uid_validity, int(imap_uid))).fetchone()
            created = existing is None
            if created:
                conn.execute("""
                    INSERT INTO mail_jobs
                        (id, account, folder, uid_validity, imap_uid, message_id,
                         raw_path, raw_sha256, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_id, account, folder, uid_validity, int(imap_uid), message_id,
                    raw_path, raw_sha256, JobStatus.PENDING.value, now, now,
                ))
                self._append_event(
                    conn, job_id, "discovered", "", JobStatus.PENDING.value,
                    metadata={"imap_uid": int(imap_uid), "uid_validity": uid_validity},
                )
            self._advance_cursor(conn, account, folder, uid_validity, imap_uid, now)
            row_id = job_id if created else existing["id"]
            row = conn.execute("SELECT * FROM mail_jobs WHERE id=?", (row_id,)).fetchone()
        return row, created

    def get_job(self, job_id: str):
        with self._connect() as conn:
            return conn.execute("SELECT * FROM mail_jobs WHERE id=?", (job_id,)).fetchone()

    def get_delivery(self, delivery_id: str):
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM outbound_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()

    def require_delivery(self, delivery_id: str, expected=None):
        row = self.get_delivery(delivery_id)
        if not row:
            raise KeyError(delivery_id)
        if expected is not None:
            expected_value = expected.value if isinstance(expected, DeliveryStatus) else str(expected)
            if row["status"] != expected_value:
                if row["status"] == DeliveryStatus.UNCERTAIN.value:
                    raise ValueError("投递结果未知，必须先由人工确认")
                raise ValueError(
                    f"投递状态不是 {expected_value}，而是 {row['status']}"
                )
        return row

    def get_events(self, job_id: str):
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM mail_job_events WHERE job_id=? ORDER BY id", (job_id,)
            ).fetchall()

    def count_jobs(self):
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM mail_jobs").fetchone()[0]

    def claim_next(self, worker_id: str, lease_seconds: int):
        now_value = self.clock()
        now = self.timestamp(now_value)
        lease = self.timestamp(now_value + timedelta(seconds=lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT * FROM mail_jobs
                WHERE status=? OR (status=? AND next_attempt_at<=?)
                ORDER BY created_at, imap_uid LIMIT 1
            """, (JobStatus.PENDING.value, JobStatus.RETRY_WAIT.value, now)).fetchone()
            if not row:
                return None
            updated = conn.execute("""
                UPDATE mail_jobs SET status=?, lease_owner=?, lease_expires_at=?,
                    attempt_count=attempt_count+1, updated_at=?
                WHERE id=? AND status=?
            """, (
                JobStatus.PROCESSING.value, worker_id, lease, now,
                row["id"], row["status"],
            ))
            if updated.rowcount != 1:
                return None
            self._append_event(
                conn, row["id"], "claimed", row["status"],
                JobStatus.PROCESSING.value, worker_id,
            )
            return conn.execute(
                "SELECT * FROM mail_jobs WHERE id=?", (row["id"],)
            ).fetchone()

    def complete_job(self, job_id: str, target, actor: str = ""):
        target_value = target.value if isinstance(target, JobStatus) else str(target)
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM mail_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            require_transition(row["status"], target_value, JOB_TRANSITIONS)
            finished_at = now if target_value in {
                JobStatus.DRAFT_READY.value, JobStatus.ESCALATED.value,
                JobStatus.SENT.value, JobStatus.DEAD_LETTER.value,
            } else None
            conn.execute("""
                UPDATE mail_jobs SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                    next_attempt_at=NULL, updated_at=?, finished_at=? WHERE id=?
            """, (target_value, now, finished_at, job_id))
            self._append_event(
                conn, job_id, "completed", row["status"], target_value, actor,
            )
            return conn.execute("SELECT * FROM mail_jobs WHERE id=?", (job_id,)).fetchone()

    def fail_job(self, job_id: str, error: str, max_attempts: int,
                 retry_delays, actor: str = ""):
        now_value = self.clock()
        now = self.timestamp(now_value)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM mail_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            attempts = int(row["attempt_count"])
            target = (JobStatus.DEAD_LETTER if attempts >= max_attempts
                      else JobStatus.RETRY_WAIT)
            require_transition(row["status"], target.value, JOB_TRANSITIONS)
            next_attempt = None
            if target == JobStatus.RETRY_WAIT:
                index = min(max(attempts - 1, 0), len(retry_delays) - 1)
                next_attempt = self.timestamp(
                    now_value + timedelta(seconds=int(retry_delays[index]))
                )
            conn.execute("""
                UPDATE mail_jobs SET status=?, next_attempt_at=?, lease_owner=NULL,
                    lease_expires_at=NULL, last_error=?, updated_at=?, finished_at=?
                WHERE id=?
            """, (
                target.value, next_attempt, str(error)[:1000], now,
                now if target == JobStatus.DEAD_LETTER else None, job_id,
            ))
            self._append_event(
                conn, job_id,
                "dead_lettered" if target == JobStatus.DEAD_LETTER else "retry_scheduled",
                row["status"], target.value, actor, str(error)[:1000],
                metadata={"attempt_count": attempts, "next_attempt_at": next_attempt},
            )
        return target

    def create_delivery(self, *, job_id, business_message_id, email_message_id,
                        recipient, subject, body, body_sha256, created_by,
                        in_reply_to="", supersedes_id=None):
        delivery_id = uuid.uuid4().hex
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if job_id:
                job = conn.execute("SELECT * FROM mail_jobs WHERE id=?", (job_id,)).fetchone()
                if not job:
                    raise KeyError(job_id)
                existing = conn.execute("""
                    SELECT id FROM outbound_deliveries
                    WHERE job_id=? AND status IN ('prepared','sending','uncertain','accepted')
                    LIMIT 1
                """, (job_id,)).fetchone()
                if existing and not supersedes_id:
                    raise ValueError("该任务已经存在投递记录")
                require_transition(job["status"], JobStatus.SEND_PREPARED.value,
                                   JOB_TRANSITIONS)
                conn.execute(
                    "UPDATE mail_jobs SET status=?, updated_at=? WHERE id=?",
                    (JobStatus.SEND_PREPARED.value, now, job_id),
                )
            try:
                conn.execute("""
                    INSERT INTO outbound_deliveries
                        (id, job_id, business_message_id, email_message_id, recipient,
                         subject, body, in_reply_to, body_sha256, status, created_by,
                         supersedes_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    delivery_id, job_id, business_message_id, email_message_id,
                    recipient, subject, body, in_reply_to, body_sha256,
                    DeliveryStatus.PREPARED.value, created_by, supersedes_id, now,
                ))
            except sqlite3.IntegrityError as exc:
                raise ValueError("该邮件已经存在活动投递记录") from exc
            self._append_event(
                conn, job_id, "delivery_prepared",
                job["status"] if job_id else "",
                JobStatus.SEND_PREPARED.value if job_id else "",
                created_by, delivery_id=delivery_id,
            )
        return delivery_id

    def mark_delivery_sending(self, delivery_id: str, lease_seconds: int):
        now_value = self.clock()
        now = self.timestamp(now_value)
        lease = self.timestamp(now_value + timedelta(seconds=lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivery = conn.execute(
                "SELECT * FROM outbound_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            if not delivery:
                raise KeyError(delivery_id)
            require_transition(delivery["status"], DeliveryStatus.SENDING.value,
                               DELIVERY_TRANSITIONS)
            conn.execute("""
                UPDATE outbound_deliveries SET status=?, started_at=?,
                    lease_expires_at=? WHERE id=?
            """, (DeliveryStatus.SENDING.value, now, lease, delivery_id))
            if delivery["job_id"]:
                job = conn.execute(
                    "SELECT * FROM mail_jobs WHERE id=?", (delivery["job_id"],)
                ).fetchone()
                require_transition(job["status"], JobStatus.SENDING.value, JOB_TRANSITIONS)
                conn.execute("""
                    UPDATE mail_jobs SET status=?, lease_expires_at=?, updated_at=?
                    WHERE id=?
                """, (JobStatus.SENDING.value, lease, now, job["id"]))
            self._append_event(
                conn, delivery["job_id"], "delivery_started",
                DeliveryStatus.PREPARED.value, DeliveryStatus.SENDING.value,
                delivery_id=delivery_id,
            )

    def accept_delivery(self, delivery_id: str, smtp_response: str = ""):
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivery = conn.execute(
                "SELECT * FROM outbound_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            if not delivery:
                raise KeyError(delivery_id)
            require_transition(delivery["status"], DeliveryStatus.ACCEPTED.value,
                               DELIVERY_TRANSITIONS)
            conn.execute("""
                UPDATE outbound_deliveries SET status=?, smtp_response=?,
                    lease_expires_at=NULL, completed_at=? WHERE id=?
            """, (
                DeliveryStatus.ACCEPTED.value, str(smtp_response)[:1000], now, delivery_id,
            ))
            if delivery["job_id"]:
                job = conn.execute(
                    "SELECT * FROM mail_jobs WHERE id=?", (delivery["job_id"],)
                ).fetchone()
                require_transition(job["status"], JobStatus.SENT.value, JOB_TRANSITIONS)
                conn.execute("""
                    UPDATE mail_jobs SET status=?, lease_owner=NULL,
                        lease_expires_at=NULL, updated_at=?, finished_at=? WHERE id=?
                """, (JobStatus.SENT.value, now, now, job["id"]))
            conn.execute("""
                UPDATE processed_emails SET status='replied', draft_text=?,
                    replied_at=?, last_error='' WHERE message_id=?
            """, (delivery["body"], now, delivery["business_message_id"]))
            mail = conn.execute(
                "SELECT sender FROM processed_emails WHERE message_id=?",
                (delivery["business_message_id"],),
            ).fetchone()
            if mail:
                already = conn.execute("""
                    SELECT 1 FROM conversation_history
                    WHERE message_id=? AND role='agent' AND content=? LIMIT 1
                """, (delivery["business_message_id"], delivery["body"])).fetchone()
                if not already:
                    conn.execute("""
                        INSERT INTO conversation_history
                            (sender_email, message_id, role, content, created_at)
                        VALUES (?, ?, 'agent', ?, ?)
                    """, (
                        mail["sender"], delivery["business_message_id"],
                        delivery["body"], now,
                    ))
            self._append_event(
                conn, delivery["job_id"], "delivery_accepted",
                delivery["status"], DeliveryStatus.ACCEPTED.value,
                delivery_id=delivery_id,
            )

    def fail_delivery_safely(self, delivery_id: str, detail: str = ""):
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivery = conn.execute(
                "SELECT * FROM outbound_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            if not delivery:
                raise KeyError(delivery_id)
            require_transition(delivery["status"], DeliveryStatus.FAILED_SAFE.value,
                               DELIVERY_TRANSITIONS)
            conn.execute("""
                UPDATE outbound_deliveries SET status=?, smtp_response=?,
                    lease_expires_at=NULL, completed_at=? WHERE id=?
            """, (
                DeliveryStatus.FAILED_SAFE.value, str(detail)[:1000], now, delivery_id,
            ))
            self._append_event(
                conn, delivery["job_id"], "delivery_failed_safe", delivery["status"],
                DeliveryStatus.FAILED_SAFE.value, reason=detail,
                delivery_id=delivery_id,
            )

    def mark_delivery_uncertain(self, delivery_id: str, detail: str = ""):
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivery = conn.execute(
                "SELECT * FROM outbound_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            if not delivery:
                raise KeyError(delivery_id)
            require_transition(delivery["status"], DeliveryStatus.UNCERTAIN.value,
                               DELIVERY_TRANSITIONS)
            conn.execute("""
                UPDATE outbound_deliveries SET status=?, smtp_response=?,
                    lease_expires_at=NULL, completed_at=? WHERE id=?
            """, (
                DeliveryStatus.UNCERTAIN.value, str(detail)[:1000], now, delivery_id,
            ))
            if delivery["job_id"]:
                job = conn.execute(
                    "SELECT * FROM mail_jobs WHERE id=?", (delivery["job_id"],)
                ).fetchone()
                require_transition(
                    job["status"], JobStatus.AWAITING_CONFIRMATION.value, JOB_TRANSITIONS
                )
                conn.execute("""
                    UPDATE mail_jobs SET status=?, lease_owner=NULL,
                        lease_expires_at=NULL, updated_at=? WHERE id=?
                """, (JobStatus.AWAITING_CONFIRMATION.value, now, job["id"]))
            self._append_event(
                conn, delivery["job_id"], "delivery_uncertain", delivery["status"],
                DeliveryStatus.UNCERTAIN.value, reason=detail,
                delivery_id=delivery_id,
            )

    def cancel_uncertain_delivery(self, delivery_id: str, actor: str, reason: str):
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivery = conn.execute(
                "SELECT * FROM outbound_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            if not delivery:
                raise KeyError(delivery_id)
            require_transition(delivery["status"], DeliveryStatus.CANCELLED.value,
                               DELIVERY_TRANSITIONS)
            conn.execute("""
                UPDATE outbound_deliveries SET status=?, completed_at=? WHERE id=?
            """, (DeliveryStatus.CANCELLED.value, now, delivery_id))
            self._append_event(
                conn, delivery["job_id"], "delivery_resend_authorized",
                delivery["status"], DeliveryStatus.CANCELLED.value,
                actor, reason, delivery_id,
            )
        return delivery

    def recover_expired(self):
        now_value = self.clock()
        now = self.timestamp(now_value)
        retry_at = self.timestamp(now_value + timedelta(seconds=60))
        recovered = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("""
                SELECT * FROM mail_jobs
                WHERE status IN ('processing','sending')
                  AND lease_expires_at IS NOT NULL AND lease_expires_at<=?
                ORDER BY created_at
            """, (now,)).fetchall()
            for row in rows:
                if row["status"] == JobStatus.PROCESSING.value:
                    target = JobStatus.RETRY_WAIT.value
                    conn.execute("""
                        UPDATE mail_jobs SET status=?, next_attempt_at=?,
                            lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                        WHERE id=?
                    """, (target, retry_at, now, row["id"]))
                    event = "lease_recovered"
                else:
                    target = JobStatus.AWAITING_CONFIRMATION.value
                    conn.execute("""
                        UPDATE mail_jobs SET status=?, lease_owner=NULL,
                            lease_expires_at=NULL, updated_at=? WHERE id=?
                    """, (target, now, row["id"]))
                    deliveries = conn.execute("""
                        SELECT id FROM outbound_deliveries
                        WHERE job_id=? AND status='sending'
                    """, (row["id"],)).fetchall()
                    for delivery in deliveries:
                        conn.execute("""
                            UPDATE outbound_deliveries SET status=?, lease_expires_at=NULL,
                                completed_at=?, smtp_response=? WHERE id=?
                        """, (
                            DeliveryStatus.UNCERTAIN.value, now,
                            "进程在发送阶段中断，结果未知", delivery["id"],
                        ))
                    event = "delivery_recovery_required"
                self._append_event(
                    conn, row["id"], event, row["status"], target,
                    reason="租约过期恢复",
                )
                recovered.append(row["id"])
        return recovered
