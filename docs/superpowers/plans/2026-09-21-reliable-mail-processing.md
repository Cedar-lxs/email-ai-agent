# Reliable Mail Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a SQLite-backed, crash-recoverable inbound mail queue and outbound delivery ledger that prevents automatic duplicate replies and exposes failures for human resolution.

**Architecture:** Keep `processed_emails` as the business-facing mail record, and add focused components for job persistence, raw-message spooling, UID ingestion, job execution, and reliable SMTP delivery. IMAP discovery persists a raw `.eml` and job before advancing a UID cursor; outbound delivery persists a fixed RFC Message-ID before SMTP and converts any post-start ambiguity into an operator-only confirmation state.

**Tech Stack:** Python 3.10+, SQLite/WAL, `imaplib`, `smtplib`, Flask, Vue 3, Element Plus, `unittest`/`pytest`, Vite.

**Spec:** `docs/superpowers/specs/2026-09-21-reliable-mail-processing-design.md`

## Global Constraints

- Use the existing SQLite database at `database.path`; do not add Redis, Celery, or another external queue.
- Keep all existing `processed_emails`, authentication, attachment, media, draft, and knowledge data intact; do not backfill historical emails into active jobs.
- Use `(account, folder, uid_validity, imap_uid)` as the inbound uniqueness key and `Message-ID` only for business correlation and conservative duplicate suppression.
- Advance the IMAP cursor only after the raw `.eml` and SQLite job are durable.
- Once a delivery reaches `sending`, no automatic path may resend it; stale or exceptional sends become `uncertain`/`awaiting_confirmation`.
- Automatic and human-approved replies must use the same `DeliveryService` and outbound ledger.
- Raw messages live below `get_project_paths().data / "inbox_spool"` and are never exposed through the static file server.
- Preserve `semi_auto` as the default rollout mode and retain compatibility with Python 3.10.
- Keep test doubles local: all IMAP, SMTP, AI, and browser tests must run without external network access.

## Review Focus

- A UIDVALIDITY reset that presents the same RFC Message-ID under a new UID must create an auditable job but never a second automatic delivery; Task 6 adds this integration test.
- Missing, malformed, or non-numeric UIDVALIDITY responses must stop ingestion without changing the stored cursor; Task 3 adds this parser/failure test.
- A disk/write/rename failure while spooling a message must leave the cursor behind that UID so the next run can recover it; Task 3 adds this failure-injection test.
- Two overlapping workers must never claim the same pending job; Task 1 adds a two-connection claim test against the same SQLite file.
- A crash after delivery enters `sending`, including the window after SMTP accepted but before local commit, must recover only to `awaiting_confirmation`; Task 4 adds stale-send recovery and no-resend tests.

---

### Task 1: SQLite Job And Delivery Ledger

**Files:**
- Create: `src/email_agent/domain/mail_jobs.py`
- Create: `src/email_agent/infrastructure/mail_job_store.py`
- Modify: `src/email_agent/infrastructure/database.py`
- Test: `tests/test_mail_job_store.py`

**Interfaces:**
- Consumes: `EmailDB.conn`, the existing SQLite/WAL database, and an injectable `clock: Callable[[], datetime]`.
- Produces: `JobStatus`, `DeliveryStatus`, `record_discovery(job_id, account, folder, uid_validity, imap_uid, message_id, raw_path, raw_sha256)`, `claim_next(worker_id, lease_seconds)`, `complete_job(job_id, target, actor)`, `fail_job(job_id, error, max_attempts, retry_delays, actor)`, `recover_expired()`, delivery transition methods, and operations query methods used by Tasks 3-7.

- [ ] **Step 1: Write failing schema, idempotency, transition, lease, and concurrency tests**

Create `tests/test_mail_job_store.py` with temporary SQLite databases and these concrete cases:

```python
def test_record_discovery_is_idempotent_and_advances_cursor_atomically(self):
    first, created = self.store.record_discovery(
        job_id="job-1", account="support@example.com", folder="INBOX",
        uid_validity="7", imap_uid=42, message_id="m1@example.com",
        raw_path="spool/job-1.eml", raw_sha256="abc",
    )
    duplicate, created_again = self.store.record_discovery(
        job_id="job-2", account="support@example.com", folder="INBOX",
        uid_validity="7", imap_uid=42, message_id="m1@example.com",
        raw_path="spool/job-2.eml", raw_sha256="abc",
    )
    self.assertTrue(created)
    self.assertFalse(created_again)
    self.assertEqual(first["id"], duplicate["id"])
    self.assertEqual(self.store.get_cursor("support@example.com", "INBOX").last_scanned_uid, 42)
    self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM mail_jobs").fetchone()[0], 1)

def test_two_store_connections_claim_a_job_only_once(self):
    other_db = EmailDB(str(self.db_path))
    other = MailJobStore(other_db, clock=self.clock)
    self._discover("job-1", 1)
    claims = [
        self.store.claim_next("worker-a", lease_seconds=60),
        other.claim_next("worker-b", lease_seconds=60),
    ]
    self.assertEqual([row["id"] for row in claims if row], ["job-1"])

def test_expired_processing_retries_but_expired_sending_requires_confirmation(self):
    self._discover("processing-job", 1)
    self.store.claim_next("worker-a", lease_seconds=1)
    self.clock.advance(seconds=2)
    self.store.recover_expired()
    self.assertEqual(self.store.get_job("processing-job")["status"], JobStatus.RETRY_WAIT)

    self._discover("sending-job", 2)
    self.store.claim_next("worker-a", lease_seconds=1)
    delivery_id = self.store.create_delivery(
        job_id="sending-job", business_message_id="m-2@example.com",
        email_message_id="<delivery-2@example.com>", recipient="customer@example.com",
        subject="Re: test", body="reply", body_sha256="hash", created_by="auto",
    )
    self.store.mark_delivery_sending(delivery_id, lease_seconds=1)
    self.clock.advance(seconds=2)
    self.store.recover_expired()
    self.assertEqual(self.store.get_delivery(delivery_id)["status"], DeliveryStatus.UNCERTAIN)
    self.assertEqual(self.store.get_job("sending-job")["status"], JobStatus.AWAITING_CONFIRMATION)
```

Also test rejected transitions, capped error text, retry delays `[60, 300, 1800]`, dead-letter after the third attempt, append-only events, a UIDVALIDITY cursor reset, and that constructing the store against a populated legacy `processed_emails` table leaves every legacy row unchanged.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_mail_job_store.py -q`

Expected: FAIL because `email_agent.domain.mail_jobs` and `MailJobStore` do not exist.

- [ ] **Step 3: Add explicit status types and legal transitions**

Create `src/email_agent/domain/mail_jobs.py` with string enums, cursor/result dataclasses, and validation:

```python
class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SEND_PREPARED = "send_prepared"
    SENDING = "sending"
    DRAFT_READY = "draft_ready"
    ESCALATED = "escalated"
    SENT = "sent"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DEAD_LETTER = "dead_letter"


class DeliveryStatus(str, Enum):
    PREPARED = "prepared"
    SENDING = "sending"
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    FAILED_SAFE = "failed_safe"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class MailboxCursor:
    account: str
    folder: str
    uid_validity: str
    last_scanned_uid: int


TERMINAL_JOB_STATUSES = {
    JobStatus.DRAFT_READY, JobStatus.ESCALATED, JobStatus.SENT, JobStatus.DEAD_LETTER,
}


def require_transition(current: str, target: str, allowed: dict[str, set[str]]) -> None:
    if target not in allowed.get(current, set()):
        raise ValueError(f"非法状态转换: {current} -> {target}")
```

Define complete transition maps. In particular, allow expired `processing -> retry_wait`, `processing -> send_prepared/draft_ready/escalated/dead_letter`, `send_prepared -> sending/retry_wait/dead_letter`, `sending -> sent/awaiting_confirmation`, and never allow `sending -> retry_wait`.

- [ ] **Step 4: Add schema and transactional repository methods**

Store `self.db_path = str(Path(db_path))` in `EmailDB`. Implement `MailJobStore` with a private connection factory so competing worker claims use separate SQLite connections:

```python
def _connect(self):
    conn = sqlite3.connect(self.db.db_path, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.row_factory = sqlite3.Row
    return conn

def claim_next(self, worker_id: str, lease_seconds: int):
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM mail_jobs
               WHERE status='pending'
                  OR (status='retry_wait' AND next_attempt_at <= ?)
               ORDER BY created_at, imap_uid LIMIT 1""",
            (self._timestamp(self.clock()),),
        ).fetchone()
        if not row:
            return None
        updated = conn.execute(
            """UPDATE mail_jobs SET status='processing', lease_owner=?,
                      lease_expires_at=?, attempt_count=attempt_count+1, updated_at=?
               WHERE id=? AND status=?""",
            (worker_id, self._timestamp(self.clock() + timedelta(seconds=lease_seconds)),
             self._timestamp(self.clock()), row["id"], row["status"]),
        )
        if updated.rowcount != 1:
            return None
        self._append_event(conn, row["id"], "claimed", row["status"], "processing", worker_id)
        return conn.execute("SELECT * FROM mail_jobs WHERE id=?", (row["id"],)).fetchone()
```

Initialize `mailbox_cursors`, `mail_jobs`, `outbound_deliveries`, and `mail_job_events` with foreign keys and indexes. `mail_jobs` includes nullable `seen_at` and `seen_result` fields so IMAP flag synchronization is independently retryable and auditable. Add a partial unique index that prevents more than one active delivery for the same business message:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_business_delivery
ON outbound_deliveries(business_message_id)
WHERE status IN ('prepared', 'sending', 'uncertain');
```

Implement every method exercised in Step 1 using `BEGIN IMMEDIATE`, compare-and-set status updates, bounded error strings, and event writes in the same transaction. `recover_expired()` must turn stale `processing` into `retry_wait`, but stale `sending` plus its active delivery into `awaiting_confirmation`/`uncertain`. `create_delivery()` must reject any second delivery for a job that already has an accepted or active delivery unless the call supplies an uncertain `supersedes_id` through the audited resend path.

- [ ] **Step 5: Run repository and legacy database tests**

Run: `python -m pytest tests/test_mail_job_store.py tests/test_decision_audit.py tests/test_auth_security.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the persistence boundary**

```powershell
git add src/email_agent/domain/mail_jobs.py src/email_agent/infrastructure/mail_job_store.py src/email_agent/infrastructure/database.py tests/test_mail_job_store.py
git commit -m "feat: add durable mail job ledger"
```

### Task 2: Atomic Raw Mail Spool

**Files:**
- Create: `src/email_agent/infrastructure/raw_mail_spool.py`
- Test: `tests/test_raw_mail_spool.py`

**Interfaces:**
- Consumes: a spool root `Path`, a generated job ID, raw RFC 822 bytes, and an injectable clock.
- Produces: `StoredRawMail(path: str, sha256: str, size_bytes: int)`, `store(job_id, raw_bytes)`, `read(stored_path)`, `discard(stored_path)`, and `cleanup_orphans(referenced_paths, grace_seconds)` used by Tasks 3 and 5.

- [ ] **Step 1: Write failing atomic-write and orphan-cleanup tests**

```python
def test_store_uses_generated_name_and_returns_verified_hash(self):
    stored = self.spool.store("job-123", b"Subject: Test\r\n\r\nBody")
    self.assertEqual(Path(stored.path).name, "job-123.eml")
    self.assertEqual(self.spool.read(stored.path), b"Subject: Test\r\n\r\nBody")
    self.assertEqual(stored.sha256, hashlib.sha256(self.spool.read(stored.path)).hexdigest())
    self.assertFalse(list(self.root.rglob("*.tmp")))

def test_failed_replace_removes_temporary_file_and_leaves_no_final(self):
    with patch("email_agent.infrastructure.raw_mail_spool.os.replace", side_effect=OSError("disk")):
        with self.assertRaisesRegex(OSError, "disk"):
            self.spool.store("job-123", b"raw")
    self.assertFalse(list(self.root.rglob("*job-123*")))

def test_cleanup_only_deletes_unreferenced_files_older_than_grace_period(self):
    old_orphan = self._write_with_age("old.eml", days=2)
    old_referenced = self._write_with_age("kept.eml", days=2)
    fresh_orphan = self._write_with_age("fresh.eml", minutes=1)
    deleted = self.spool.cleanup_orphans({str(old_referenced)}, grace_seconds=3600)
    self.assertEqual(deleted, [str(old_orphan)])
    self.assertTrue(old_referenced.exists())
    self.assertTrue(fresh_orphan.exists())
```

Also verify path traversal strings cannot escape the root and `read()` rejects database paths outside the spool root.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_raw_mail_spool.py -q`

Expected: FAIL because `RawMailSpool` does not exist.

- [ ] **Step 3: Implement fsync plus atomic replace**

```python
@dataclass(frozen=True)
class StoredRawMail:
    path: str
    sha256: str
    size_bytes: int


def store(self, job_id: str, raw_bytes: bytes) -> StoredRawMail:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", job_id)
    month_dir = self.root / self.clock().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    target = month_dir / f"{safe_id}.eml"
    temporary = month_dir / f".{safe_id}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(raw_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return StoredRawMail(str(target), hashlib.sha256(raw_bytes).hexdigest(), len(raw_bytes))
```

Resolve and validate paths before reads/deletes. `cleanup_orphans()` must compare resolved absolute paths and only remove `.eml` files older than `grace_seconds` that are absent from the referenced set.

- [ ] **Step 4: Run spool tests**

Run: `python -m pytest tests/test_raw_mail_spool.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the spool**

```powershell
git add src/email_agent/infrastructure/raw_mail_spool.py tests/test_raw_mail_spool.py
git commit -m "feat: spool raw inbound mail atomically"
```

### Task 3: UID-Based IMAP Ingestion

**Files:**
- Create: `src/email_agent/application/mail_ingestion.py`
- Modify: `src/email_agent/infrastructure/mail_fetcher.py`
- Modify: `src/email_agent/domain/models.py`
- Test: `tests/test_mail_ingestion.py`
- Modify: `tests/test_email_service.py`

**Interfaces:**
- Consumes: `MailFetcher.select_folder(folder)`, `search_uids(after_uid)`, `fetch_uid(uid)`, `RawMailSpool`, and `MailJobStore`.
- Produces: `IngestionResult(discovered_job_ids, scanned_uids, uid_validity)`, durable cursor progression, and compatibility parsing via `MailFetcher.parse_raw_email(raw_bytes)`.

- [ ] **Step 1: Write failing UID command and ingestion tests**

Use a fake IMAP connection that records `uid()` calls:

```python
def test_fetcher_uses_uid_search_fetch_and_store(self):
    connection = FakeIMAP(uid_validity=b"91", search_result=b"101 102")
    fetcher = self._fetcher(connection)
    self.assertEqual(fetcher.select_folder("INBOX"), "91")
    self.assertEqual(fetcher.search_uids(after_uid=100), [101, 102])
    self.assertIn(("SEARCH", None, "UID 101:*"), connection.uid_calls)
    self.assertEqual(fetcher.fetch_uid(101), RAW_EMAIL)
    fetcher.mark_uid_seen(101)
    self.assertIn(("STORE", "101", "+FLAGS", "\\Seen"), connection.uid_calls)

def test_malformed_uidvalidity_stops_without_changing_cursor(self):
    self.store.reset_cursor("support@example.com", "INBOX", "90")
    self.store.advance_cursor("support@example.com", "INBOX", "90", 100)
    fetcher = FakeFetcher(uid_validity="not-a-number")
    with self.assertRaisesRegex(RuntimeError, "UIDVALIDITY"):
        self.ingestion.ingest(fetcher)
    self.assertEqual(self.store.get_cursor("support@example.com", "INBOX").last_scanned_uid, 100)

def test_spool_failure_does_not_advance_past_failed_uid(self):
    fetcher = FakeFetcher(uid_validity="91", uids=[101, 102])
    self.spool.store = Mock(side_effect=[StoredRawMail("101.eml", "a", 10), OSError("disk full")])
    with self.assertRaisesRegex(OSError, "disk full"):
        self.ingestion.ingest(fetcher)
    self.assertEqual(self.store.get_cursor("support@example.com", "INBOX").last_scanned_uid, 101)

def test_uidvalidity_change_resets_uid_scan_but_keeps_old_jobs(self):
    self._set_cursor(uid_validity="90", uid=500)
    result = self.ingestion.ingest(FakeFetcher(uid_validity="91", uids=[1]))
    self.assertEqual(result.scanned_uids, (1,))
    self.assertEqual(self.store.count_jobs(), 1)
    self.assertEqual(self.store.get_cursor("support@example.com", "INBOX").uid_validity, "91")
```

Also test duplicate discovery deletes the newly written unused spool file, batch-size limiting, ascending UID order, missing `Message-ID` falling back to the raw SHA-256, and that `BODY.PEEK[]` never marks a message seen.

- [ ] **Step 2: Run ingestion and existing parser tests and verify the new tests fail**

Run: `python -m pytest tests/test_mail_ingestion.py tests/test_multimodal_mail_fetcher.py tests/test_attachment_mail_fetcher.py -q`

Expected: new ingestion tests FAIL; existing MIME parser tests still PASS.

- [ ] **Step 3: Add stable UID primitives while retaining compatibility wrappers**

Implement these methods in `MailFetcher`:

```python
def select_folder(self, folder: str = "INBOX") -> str:
    status, _ = self._conn.select(folder)
    if status != "OK":
        raise RuntimeError(f"无法选择邮箱文件夹: {folder}")
    _, values = self._conn.response("UIDVALIDITY")
    value = values[0].decode("ascii") if values and values[0] else ""
    if not value.isdigit():
        raise RuntimeError("IMAP 未返回有效 UIDVALIDITY")
    return value

def search_uids(self, after_uid: int) -> list[int]:
    status, data = self._conn.uid("SEARCH", None, f"UID {max(1, after_uid + 1)}:*")
    if status != "OK":
        raise RuntimeError("IMAP UID 搜索失败")
    return sorted(int(value) for value in (data[0] or b"").split())

def fetch_uid(self, uid: int) -> bytes:
    status, data = self._conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
    if status != "OK" or not data or not isinstance(data[0], tuple):
        raise RuntimeError(f"IMAP UID {uid} 拉取失败")
    return data[0][1]
```

Expose `parse_raw_email()` as a public wrapper around the existing MIME parser. Keep `fetch_unread()` and `mark_seen()` temporarily for existing callers/tests, but the new orchestration must not call them.

- [ ] **Step 4: Implement durable ingestion**

Create `MailIngestionService` with this control flow:

```python
def ingest(self, fetcher: MailFetcher) -> IngestionResult:
    uid_validity = fetcher.select_folder(self.folder)
    cursor = self.store.get_cursor(self.account, self.folder)
    if cursor and cursor.uid_validity != uid_validity:
        self.store.reset_cursor(self.account, self.folder, uid_validity)
        after_uid = 0
    else:
        after_uid = cursor.last_scanned_uid if cursor else 0

    discovered, scanned = [], []
    for uid in fetcher.search_uids(after_uid)[:self.batch_size]:
        raw = fetcher.fetch_uid(uid)
        job_id = uuid.uuid4().hex
        stored = self.spool.store(job_id, raw)
        message_id = MailFetcher.extract_message_id(raw, stored.sha256)
        row, created = self.store.record_discovery(
            job_id=job_id, account=self.account, folder=self.folder,
            uid_validity=uid_validity, imap_uid=uid, message_id=message_id,
            raw_path=stored.path, raw_sha256=stored.sha256,
        )
        if created:
            discovered.append(row["id"])
        else:
            self.spool.discard(stored.path)
        scanned.append(uid)
    return IngestionResult(tuple(discovered), tuple(scanned), uid_validity)
```

Cursor reset, job insert, duplicate handling, and event creation remain store responsibilities. Do not catch spool or fetch errors inside the loop; stopping preserves the cursor at the last committed UID.

- [ ] **Step 5: Run ingestion and parser tests**

Run: `python -m pytest tests/test_mail_ingestion.py tests/test_multimodal_mail_fetcher.py tests/test_attachment_mail_fetcher.py tests/test_email_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit UID ingestion**

```powershell
git add src/email_agent/application/mail_ingestion.py src/email_agent/infrastructure/mail_fetcher.py src/email_agent/domain/models.py tests/test_mail_ingestion.py tests/test_email_service.py
git commit -m "feat: ingest mail by durable imap uid"
```

### Task 4: Reliable SMTP Delivery Service

**Files:**
- Create: `src/email_agent/application/delivery_service.py`
- Modify: `src/email_agent/infrastructure/mail_sender.py`
- Modify: `src/email_agent/infrastructure/mail_job_store.py`
- Test: `tests/test_delivery_service.py`
- Modify: `tests/test_reply_policy.py`

**Interfaces:**
- Consumes: `MailJobStore`, `MailSender.build_reply_message(to_address, subject, body, in_reply_to, email_message_id)`, `MailSender.deliver_message(message, timeout, before_send)`, and persisted delivery rows.
- Produces: `SmtpOutcome`, `SmtpDeliveryResult`, `prepare_reply(job_id, business_message_id, recipient, subject, body, in_reply_to, created_by, supersedes_id=None)`, `send_prepared(delivery_id)`, `confirm_sent(delivery_id, actor, reason)`, and `authorize_resend(delivery_id, actor, reason)` used by Tasks 5-7.

- [ ] **Step 1: Write failing fixed-ID, safe-failure, uncertainty, and recovery tests**

```python
def test_prepare_persists_fixed_message_id_before_smtp(self):
    delivery_id = self.service.prepare_reply(
        job_id="job-1", business_message_id="inbound@example.com",
        recipient="customer@example.com", subject="Question", body="Answer",
        in_reply_to="inbound@example.com", created_by="auto",
    )
    row = self.store.get_delivery(delivery_id)
    self.assertEqual(row["status"], "prepared")
    self.assertRegex(row["email_message_id"], r"^<ea-[0-9a-f]+@.+>$")
    self.sender.deliver_message.assert_not_called()

def test_pre_send_failure_is_retryable_but_send_exception_is_uncertain(self):
    safe_id = self._prepared("safe")
    self.sender.deliver_message.return_value = SmtpDeliveryResult(SmtpOutcome.SAFE_FAILURE, "login")
    self.assertEqual(self.service.send_prepared(safe_id).outcome, SmtpOutcome.SAFE_FAILURE)
    self.assertEqual(self.store.get_delivery(safe_id)["status"], "failed_safe")

    uncertain_id = self._prepared("uncertain")
    def fail_after_start(message, timeout, before_send):
        before_send()
        return SmtpDeliveryResult(SmtpOutcome.UNCERTAIN, "timeout")
    self.sender.deliver_message.side_effect = fail_after_start
    self.assertEqual(self.service.send_prepared(uncertain_id).outcome, SmtpOutcome.UNCERTAIN)
    self.assertEqual(self.store.get_delivery(uncertain_id)["status"], "uncertain")
    self.assertEqual(self.store.get_job("job-1")["status"], "awaiting_confirmation")

def test_stale_sending_is_never_called_again_automatically(self):
    delivery_id = self._prepared("crash-window")
    self.store.mark_delivery_sending(delivery_id, lease_seconds=1)
    self.clock.advance(seconds=2)
    self.store.recover_expired()
    with self.assertRaisesRegex(ValueError, "人工确认"):
        self.service.send_prepared(delivery_id)
    self.sender.deliver_message.assert_not_called()
```

Also test that accepted delivery atomically updates `outbound_deliveries`, `mail_jobs`, `processed_emails`, and `conversation_history`; a second active or accepted delivery for the same job is blocked; manual `authorize_resend()` requires non-empty actor and reason, creates a new RFC Message-ID, and records `supersedes_id`.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `python -m pytest tests/test_delivery_service.py tests/test_reply_policy.py -q`

Expected: FAIL because the delivery service and structured SMTP result do not exist.

- [ ] **Step 3: Split message construction from SMTP transport**

Add transport outcomes and make the SMTP phase boundary explicit:

```python
class SmtpOutcome(str, Enum):
    ACCEPTED = "accepted"
    SAFE_FAILURE = "safe_failure"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class SmtpDeliveryResult:
    outcome: SmtpOutcome
    detail: str = ""


def deliver_message(self, message, timeout: float, before_send) -> SmtpDeliveryResult:
    try:
        smtp = smtplib.SMTP_SSL(self.server, self.port, timeout=timeout)
        smtp.login(self.account, self.password)
    except Exception as exc:
        return SmtpDeliveryResult(SmtpOutcome.SAFE_FAILURE, self._safe_error(exc))
    started = False
    try:
        started = True
        before_send()
        smtp.send_message(message)
        return SmtpDeliveryResult(SmtpOutcome.ACCEPTED)
    except Exception as exc:
        outcome = SmtpOutcome.UNCERTAIN if started else SmtpOutcome.SAFE_FAILURE
        return SmtpDeliveryResult(outcome, self._safe_error(exc))
    finally:
        try:
            smtp.quit()
        except Exception:
            pass
```

`build_reply_message()` must accept and set the persisted `email_message_id`. The `before_send` callback is the conservative boundary that persists `sending`: connection/authentication failures happen before it and are safe to retry, while every exception from the callback onward is uncertain. This intentionally sends some database failures to human confirmation even when SMTP was probably not reached, because duplicate prevention has priority. Retain `send_reply()` as a compatibility wrapper only until Task 6 removes production callers.

- [ ] **Step 4: Implement prepare/send/resolve transitions**

```python
def send_prepared(self, delivery_id: str) -> SmtpDeliveryResult:
    delivery = self.store.require_delivery(delivery_id, DeliveryStatus.PREPARED)
    message = self.sender.build_reply_message(
        delivery["recipient"], delivery["subject"], delivery["body"],
        delivery["in_reply_to"], delivery["email_message_id"],
    )
    result = self.sender.deliver_message(
        message,
        timeout=self.smtp_timeout,
        before_send=lambda: self.store.mark_delivery_sending(
            delivery_id, self.send_lease_seconds
        ),
    )
    if result.outcome == SmtpOutcome.ACCEPTED:
        self.store.accept_delivery(delivery_id, result.detail)
    elif result.outcome == SmtpOutcome.SAFE_FAILURE:
        self.store.fail_delivery_safely(delivery_id, result.detail)
    else:
        self.store.mark_delivery_uncertain(delivery_id, result.detail)
    return result
```

`accept_delivery()` must use one SQLite transaction to mark the delivery accepted, set a linked job to `sent`, update `processed_emails` to `replied`, and insert the agent conversation exactly once. `confirm_sent()` follows the same transaction without another SMTP call. `authorize_resend()` requires a currently uncertain delivery, appends an audit event, cancels/resolves the active uncertainty, creates a superseding prepared row, then explicitly sends that new row.

- [ ] **Step 5: Run delivery and policy tests**

Run: `python -m pytest tests/test_delivery_service.py tests/test_reply_policy.py tests/test_review_service.py -q`

Expected: delivery and policy tests PASS; review tests may still use the compatibility wrapper and must remain PASS.

- [ ] **Step 6: Commit reliable delivery**

```powershell
git add src/email_agent/application/delivery_service.py src/email_agent/infrastructure/mail_sender.py src/email_agent/infrastructure/mail_job_store.py tests/test_delivery_service.py tests/test_reply_policy.py
git commit -m "feat: add duplicate-safe smtp delivery ledger"
```

### Task 5: Recoverable Job Worker

**Files:**
- Create: `src/email_agent/application/mail_worker.py`
- Modify: `src/email_agent/infrastructure/mail_job_store.py`
- Test: `tests/test_mail_worker.py`

**Interfaces:**
- Consumes: `MailJobStore.claim_next(worker_id, lease_seconds)`, `RawMailSpool.read(stored_path)`, an injected parser `Callable[[bytes], ParsedEmail]`, and an async handler `Callable[[ParsedEmail, str], Awaitable[JobStatus]]`.
- Produces: `MailJobWorker.run_available(handler) -> list[JobRunResult]`, bounded concurrency, retry/dead-letter transitions, and terminal UID records used by Task 6.

- [ ] **Step 1: Write failing worker lifecycle tests**

```python
async def test_worker_replays_spooled_mail_without_imap(self):
    self._discover_raw("job-1", RAW_EMAIL)
    handler = AsyncMock(return_value=JobStatus.DRAFT_READY)
    results = await self.worker.run_available(handler)
    handler.assert_awaited_once()
    parsed, job_id = handler.await_args.args
    self.assertEqual(parsed.message_id, "m1@example.com")
    self.assertEqual(job_id, "job-1")
    self.assertEqual(results[0].status, JobStatus.DRAFT_READY)

async def test_transient_handler_failure_retries_then_dead_letters(self):
    self._discover_raw("job-1", RAW_EMAIL)
    handler = AsyncMock(side_effect=RuntimeError("AI timeout"))
    await self.worker.run_available(handler)
    self.assertEqual(self.store.get_job("job-1")["status"], "retry_wait")
    self.clock.advance(seconds=60)
    await self.worker.run_available(handler)
    self.clock.advance(seconds=300)
    await self.worker.run_available(handler)
    self.assertEqual(self.store.get_job("job-1")["status"], "dead_letter")

async def test_worker_does_not_complete_job_already_moved_to_awaiting_confirmation(self):
    self._discover_raw("job-1", RAW_EMAIL)
    async def uncertain_handler(message, job_id):
        self.store.move_to_awaiting_confirmation(job_id, "delivery-1")
        return JobStatus.SENT
    await self.worker.run_available(uncertain_handler)
    self.assertEqual(self.store.get_job("job-1")["status"], "awaiting_confirmation")
```

Also test missing/tampered spool files, `max_concurrent`, worker cancellation leaving a recoverable lease, and that jobs already in terminal states are never claimed.

- [ ] **Step 2: Run worker tests and verify they fail**

Run: `python -m pytest tests/test_mail_worker.py -q`

Expected: FAIL because `MailJobWorker` does not exist.

- [ ] **Step 3: Implement bounded claiming and execution**

```python
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
                if current == JobStatus.PROCESSING:
                    self.store.complete_job(job["id"], target, actor=self.worker_id)
                return JobRunResult(job["id"], self.store.get_job(job["id"])["status"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current = self.store.get_job(job["id"])["status"]
                if current == JobStatus.AWAITING_CONFIRMATION:
                    return JobRunResult(job["id"], current, str(exc))
                status = self.store.fail_job(
                    job["id"], str(exc), self.max_attempts, self.retry_delays,
                    actor=self.worker_id,
                )
                return JobRunResult(job["id"], status, str(exc))
    return list(await asyncio.gather(*(execute(job) for job in claimed)))
```

Inject `parse_raw_email` through the worker constructor; production passes a bound `MailFetcher.parse_raw_email` method and tests pass a small parser double. Do not convert `awaiting_confirmation` back to a normal terminal status when the handler returns or raises. A missing or hash-mismatched spool file follows the same bounded retry/dead-letter path and records a stable error code.

- [ ] **Step 4: Run worker tests**

Run: `python -m pytest tests/test_mail_worker.py tests/test_mail_job_store.py tests/test_raw_mail_spool.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the worker**

```powershell
git add src/email_agent/application/mail_worker.py src/email_agent/infrastructure/mail_job_store.py tests/test_mail_worker.py
git commit -m "feat: process durable mail jobs with leases"
```

### Task 6: Integrate Ingestion, Worker, Auto-Send, And Draft Approval

**Files:**
- Modify: `src/email_agent/application/email_service.py`
- Modify: `src/email_agent/application/review_service.py`
- Modify: `src/email_agent/bootstrap.py`
- Modify: `config.yaml`
- Modify: `tests/test_email_service.py`
- Modify: `tests/test_review_service.py`
- Create: `tests/test_reliable_mail_flow.py`

**Interfaces:**
- Consumes: all components from Tasks 1-5.
- Produces: production `run_once_async()` using UID ingestion and jobs, `EmailAgent.process_job_email_async(email, job_id) -> JobStatus`, and reliable manual approval.

- [ ] **Step 1: Write failing end-to-end orchestration tests**

```python
async def test_run_once_ingests_then_processes_spool_and_marks_terminal_uid_seen(self):
    agent, fetcher = self._agent_with_uid_mail(uid_validity="7", uid=42, raw=RAW_EMAIL)
    agent._fetcher = Mock(return_value=fetcher)
    agent.process_job_email_async = AsyncMock(return_value=JobStatus.DRAFT_READY)
    results = await agent.run_once_async()
    self.assertEqual(results[0].status, JobStatus.DRAFT_READY)
    self.assertEqual(fetcher.seen_uids, [42])
    self.assertEqual(agent.mail_jobs.get_cursor(agent.mail_account, "INBOX").last_scanned_uid, 42)

async def test_same_message_id_after_uidvalidity_reset_never_auto_sends_twice(self):
    agent = self._full_auto_agent()
    await self._run_mail(agent, uid_validity="7", uid=42, message_id="same@example.com")
    self.assertEqual(agent.sender.accepted_count, 1)
    await self._run_mail(agent, uid_validity="8", uid=1, message_id="same@example.com")
    self.assertEqual(agent.sender.accepted_count, 1)
    jobs = agent.mail_jobs.find_jobs_by_message_id("same@example.com")
    self.assertEqual(len(jobs), 2)
    self.assertEqual(jobs[-1]["status"], "sent")

def test_manual_approval_uses_delivery_ledger_and_blocks_uncertain_second_click(self):
    self._create_draft("m1")
    self.sender.result = SmtpDeliveryResult(SmtpOutcome.UNCERTAIN, "timeout")
    with self.assertRaises(DeliveryUncertainError):
        self.review.approve("m1", actor="admin")
    with self.assertRaisesRegex(ValueError, "待确认"):
        self.review.approve("m1", actor="admin")
    self.assertEqual(self.sender.calls, 1)
```

Also test startup recovery runs before claiming, a processed duplicate maps to the existing business result without invoking AI, auto-send safe failure enters `retry_wait`, auto-send uncertainty enters `awaiting_confirmation`, semi-auto drafts still work, and self-sent mail terminates without reply loops.

- [ ] **Step 2: Run flow tests and verify they fail**

Run: `python -m pytest tests/test_reliable_mail_flow.py tests/test_email_service.py tests/test_review_service.py -q`

Expected: FAIL because production orchestration still uses `UNSEEN` and direct SMTP.

- [ ] **Step 3: Wire components during `EmailAgent` initialization**

Resolve all runtime paths from `paths.data`, read processing configuration with safe defaults, and create:

```python
self.mail_account = mail["account"]
self.mail_jobs = MailJobStore(self.db)
self.raw_spool = RawMailSpool(paths.data / "inbox_spool")
self.ingestion = MailIngestionService(
    self.mail_jobs, self.raw_spool, self.mail_account, "INBOX",
    batch_size=processing.get("batch_size", 20),
)
self.delivery = DeliveryService(
    self.mail_jobs, self.sender,
    smtp_timeout=processing.get("smtp_timeout", 30),
    send_lease_seconds=processing.get("lease_seconds", 600),
)
self.worker = MailJobWorker(
    self.mail_jobs, self.raw_spool, parse_raw_email=self._fetcher().parse_raw_email,
    batch_size=processing.get("batch_size", 20),
    max_concurrent=processing.get("max_concurrent", 3),
    max_attempts=processing.get("max_attempts", 3),
    retry_delays=processing.get("retry_delays_seconds", [60, 300, 1800]),
    lease_seconds=processing.get("lease_seconds", 600),
)
self.review = ReviewService(self.db, self.delivery, draft_path)
```

Add the concrete defaults to `config.yaml`, including `spool_retention_days: 30`.

- [ ] **Step 4: Replace polling orchestration and direct sends**

`run_once_async()` must recover stale work, ingest while connected, run jobs from local spool, and mark only terminal job UIDs seen:

```python
async def run_once_async(self):
    self.mail_jobs.recover_expired()
    self._cleanup_expired_spool()
    fetcher = self._fetcher()
    try:
        await fetcher.connect_async()
        await asyncio.to_thread(self.ingestion.ingest, fetcher)
    except Exception as exc:
        self.logger.error("IMAP ingest failed; durable jobs will still run: %s", exc)
    finally:
        await fetcher.disconnect_async()

    results = await self.worker.run_available(self.process_job_email_async)

    terminal = self.mail_jobs.jobs_ready_to_mark_seen()
    if terminal:
        marker = self._fetcher()
        try:
            await marker.connect_async()
            current_uid_validity = marker.select_folder("INBOX")
            for job in terminal:
                if job["uid_validity"] != current_uid_validity:
                    self.mail_jobs.record_seen(
                        job["id"], result="uidvalidity_expired"
                    )
                    continue
                await asyncio.to_thread(marker.mark_uid_seen, job["imap_uid"])
                self.mail_jobs.record_seen(job["id"], result="marked")
        except Exception as exc:
            self.logger.warning("IMAP seen sync deferred: %s", exc)
        finally:
            await marker.disconnect_async()
    return results
```

Refactor the existing processing body into `process_job_email_async(email, job_id) -> JobStatus`. All non-send paths return `DRAFT_READY` or `ESCALATED`. The auto-send branch calls `prepare_reply()` then `send_prepared()`; accepted returns `SENT`, safe failure raises a retryable exception, and uncertain raises `DeliveryUncertainError` after the store has moved the job to `awaiting_confirmation`. Add an orchestration test with IMAP connect failure plus one pre-existing pending spool job and assert the job still reaches its handler. Add another test where a terminal job belongs to an old UIDVALIDITY and assert no `UID STORE` is sent for that stale UID; its `seen_result` becomes `uidvalidity_expired`.

`_cleanup_expired_spool()` must first call `cleanup_orphans()` with all currently referenced raw paths and a 24-hour orphan grace period. It then asks the store for resolved jobs (`draft_ready`, `escalated`, or `sent`) older than `spool_retention_days`, deletes each validated spool file, clears only that job's `raw_path`, and appends a `raw_mail_pruned` event. Add a test proving pending, processing, retry, dead-letter, and awaiting-confirmation raw messages are never removed regardless of age.

Log each discovery, claim, retry, dead-letter, send start, send result, and operator transition with `job_id`, UID, business `message_id`, and `delivery_id`, but never log body text. Add queue counts and the oldest attention timestamp to the existing stats payload.

Before AI work, if `processed_emails` already contains the RFC Message-ID, map `replied -> SENT`, `draft_ready -> DRAFT_READY`, and every other completed business state to `ESCALATED`; never invoke `DeliveryService` again.

- [ ] **Step 5: Route manual approval through `DeliveryService`**

Change the signature to `ReviewService.approve(message_id: str, actor: str = "cli")`. It prepares and sends through the ledger. On `SAFE_FAILURE`, keep `draft_ready`, record the safe error, and allow a later human retry. On `UNCERTAIN`, keep the draft business record but reject subsequent approval because an active uncertain delivery exists. On accepted, rely on the store's atomic completion transaction instead of calling `update_status()` separately.

- [ ] **Step 6: Run all processing and delivery tests**

Run: `python -m pytest tests/test_reliable_mail_flow.py tests/test_email_service.py tests/test_review_service.py tests/test_delivery_service.py tests/test_multimodal_email_service.py -q`

Expected: PASS.

- [ ] **Step 7: Commit production integration**

```powershell
git add src/email_agent/application/email_service.py src/email_agent/application/review_service.py src/email_agent/bootstrap.py config.yaml tests/test_reliable_mail_flow.py tests/test_email_service.py tests/test_review_service.py
git commit -m "feat: run email processing through durable jobs"
```

### Task 7: Operations API And Audited Human Resolution

**Files:**
- Modify: `src/email_agent/web/routes/api.py`
- Modify: `src/email_agent/infrastructure/mail_job_store.py`
- Modify: `src/email_agent/application/delivery_service.py`
- Test: `tests/test_operations_api.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: `request.current_auth.username`, job/delivery list and detail queries, `requeue_dead_letter(job_id, actor, reason)`, `confirm_sent(delivery_id, actor, reason)`, `authorize_resend(delivery_id, actor, reason)`, and `escalate_job(job_id, actor, reason)`.
- Produces: authenticated operations endpoints and operation counts included in `/api/mails/stats`.

- [ ] **Step 1: Write failing authentication, validation, and action tests**

```python
def test_operations_list_requires_login_and_returns_only_requested_states(self):
    self.assertEqual(self.client.get("/api/operations/mail-jobs").status_code, 401)
    response = self.client.get(
        "/api/operations/mail-jobs?status=dead_letter,awaiting_confirmation",
        headers=self.auth_headers,
    )
    self.assertEqual(response.status_code, 200)
    self.assertEqual({item["status"] for item in response.get_json()["jobs"]},
                     {"dead_letter", "awaiting_confirmation"})

def test_confirm_sent_requires_csrf_reason_and_never_calls_smtp(self):
    delivery_id = self._uncertain_delivery()
    missing_reason = self.client.post(
        f"/api/operations/deliveries/{delivery_id}/confirm-sent",
        json={}, headers=self.auth_headers,
    )
    self.assertEqual(missing_reason.status_code, 400)
    confirmed = self.client.post(
        f"/api/operations/deliveries/{delivery_id}/confirm-sent",
        json={"reason": "已在邮箱已发送目录核对"}, headers=self.auth_headers,
    )
    self.assertEqual(confirmed.status_code, 200)
    self.delivery.sender.deliver_message.assert_not_called()

def test_authorized_resend_records_admin_reason_and_creates_new_delivery(self):
    original_id = self._uncertain_delivery()
    response = self.client.post(
        f"/api/operations/deliveries/{original_id}/authorize-resend",
        json={"reason": "邮件服务商确认原邮件未接收"}, headers=self.auth_headers,
    )
    self.assertEqual(response.status_code, 200)
    replacement = self.store.get_delivery(response.get_json()["delivery_id"])
    self.assertEqual(replacement["supersedes_id"], original_id)
    self.assertEqual(replacement["created_by"], "admin")
```

Also test CSRF rejection, unsupported state filters, page-size cap, detail event ordering, retry only from `dead_letter`, escalation only from unresolved states, and operation counts in `/api/mails/stats`.

- [ ] **Step 2: Run API tests and verify they fail**

Run: `python -m pytest tests/test_operations_api.py tests/test_web.py tests/test_auth_security.py -q`

Expected: new routes return 404.

- [ ] **Step 3: Add bounded store queries and audited commands**

Implement `list_jobs(statuses, page, page_size)`, `get_job_detail(job_id)`, `requeue_dead_letter(job_id, actor, reason)`, and `escalate_job(job_id, actor, reason)`. Clamp `page_size` to `1..100`; return a total count from SQL rather than loading every row. Requeue resets `attempt_count`, clears lease/error fields, sets `pending`, and appends an event. Escalation updates the linked business record when one exists.

- [ ] **Step 4: Add authenticated Flask routes**

Add these routes, all decorated with `@token_required`. Use one validator so every mutation has the same reason rule:

```python
def _operation_reason():
    reason = str((request.get_json(silent=True) or {}).get("reason", "")).strip()
    if not 3 <= len(reason) <= 500:
        raise ValueError("操作原因长度必须为 3-500 个字符")
    return reason


@bp.get("/operations/mail-jobs")
@token_required
def get_mail_jobs():
    raw_statuses = request.args.get("status", "retry_wait,dead_letter,awaiting_confirmation")
    statuses = tuple(value.strip() for value in raw_statuses.split(",") if value.strip())
    allowed = {"retry_wait", "dead_letter", "awaiting_confirmation"}
    if not statuses or not set(statuses) <= allowed:
        return jsonify({"error": "无效的任务状态"}), 400
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except (TypeError, ValueError):
        return jsonify({"error": "分页参数必须是整数"}), 400
    return jsonify(current_app.extensions["services"].agent.mail_jobs.list_jobs(
        statuses, page, page_size
    ))

@bp.get("/operations/mail-jobs/<job_id>")
@token_required
def get_mail_job(job_id):
    detail = current_app.extensions["services"].agent.mail_jobs.get_job_detail(job_id)
    return jsonify(detail) if detail else (jsonify({"error": "任务不存在"}), 404)

@bp.post("/operations/mail-jobs/<job_id>/retry")
@token_required
def retry_mail_job(job_id):
    try:
        row = current_app.extensions["services"].agent.mail_jobs.requeue_dead_letter(
            job_id, request.current_auth.username, _operation_reason()
        )
        return jsonify({"message": "任务已重新进入待处理队列", "job": dict(row)})
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    except ValueError as exc:
        status = 409 if "状态" in str(exc) else 400
        return jsonify({"error": str(exc)}), status

@bp.post("/operations/mail-jobs/<job_id>/escalate")
@token_required
def escalate_mail_job(job_id):
    try:
        row = current_app.extensions["services"].agent.mail_jobs.escalate_job(
            job_id, request.current_auth.username, _operation_reason()
        )
        return jsonify({"message": "任务已转人工", "job": dict(row)})
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    except ValueError as exc:
        status = 409 if "状态" in str(exc) else 400
        return jsonify({"error": str(exc)}), status

@bp.post("/operations/deliveries/<delivery_id>/confirm-sent")
@token_required
def confirm_delivery_sent(delivery_id):
    try:
        delivery = current_app.extensions["services"].agent.delivery.confirm_sent(
            delivery_id, request.current_auth.username, _operation_reason()
        )
        return jsonify({"message": "已确认发送", "delivery": dict(delivery)})
    except KeyError:
        return jsonify({"error": "投递记录不存在"}), 404
    except ValueError as exc:
        status = 409 if "状态" in str(exc) else 400
        return jsonify({"error": str(exc)}), status

@bp.post("/operations/deliveries/<delivery_id>/authorize-resend")
@token_required
def authorize_delivery_resend(delivery_id):
    try:
        replacement_id, result = current_app.extensions["services"].agent.delivery.authorize_resend(
            delivery_id, request.current_auth.username, _operation_reason()
        )
    except KeyError:
        return jsonify({"error": "投递记录不存在"}), 404
    except ValueError as exc:
        status = 409 if "状态" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    payload = {"delivery_id": replacement_id, "outcome": result.outcome.value}
    if result.outcome == SmtpOutcome.ACCEPTED:
        return jsonify(payload)
    if result.outcome == SmtpOutcome.UNCERTAIN:
        return jsonify({**payload, "error": "重发结果未知，已再次进入待确认"}), 409
    return jsonify({**payload, "error": "SMTP 尚未开始发送，可再次人工操作"}), 502
```

Read actor from `request.current_auth.username`. Require a trimmed reason of 3-500 characters for every mutating operation. Convert not-found to 404, invalid state to 409, validation errors to 400, and delivery uncertainty to 409. Add `operations: {retry_wait, dead_letter, awaiting_confirmation, total_attention}` to the existing stats response.

- [ ] **Step 5: Run API and security tests**

Run: `python -m pytest tests/test_operations_api.py tests/test_web.py tests/test_auth_security.py -q`

Expected: PASS.

- [ ] **Step 6: Commit operations APIs**

```powershell
git add src/email_agent/web/routes/api.py src/email_agent/infrastructure/mail_job_store.py src/email_agent/application/delivery_service.py tests/test_operations_api.py tests/test_web.py
git commit -m "feat: add audited mail operations api"
```

### Task 8: Operations UI, Documentation, And Full Verification

**Files:**
- Create: `frontend/src/api/operations.js`
- Create: `frontend/src/views/Operations.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/views/Layout.vue`
- Modify: `README.md`
- Modify: `src/email_agent/web/dist/**` (generated by Vite build)

**Interfaces:**
- Consumes: Task 7 operations endpoints and `/api/mails/stats.operations`.
- Produces: a responsive operator queue for retry, escalation, sent confirmation, and explicitly authorized resend.

- [ ] **Step 1: Add the frontend API wrapper**

Create `frontend/src/api/operations.js`:

```javascript
import request from '@/utils/request'

export const operationsApi = {
  list(params) { return request.get('/operations/mail-jobs', { params }) },
  detail(jobId) { return request.get(`/operations/mail-jobs/${encodeURIComponent(jobId)}`) },
  retry(jobId, reason) { return request.post(`/operations/mail-jobs/${encodeURIComponent(jobId)}/retry`, { reason }) },
  escalate(jobId, reason) { return request.post(`/operations/mail-jobs/${encodeURIComponent(jobId)}/escalate`, { reason }) },
  confirmSent(deliveryId, reason) { return request.post(`/operations/deliveries/${encodeURIComponent(deliveryId)}/confirm-sent`, { reason }) },
  authorizeResend(deliveryId, reason) { return request.post(`/operations/deliveries/${encodeURIComponent(deliveryId)}/authorize-resend`, { reason }) }
}
```

- [ ] **Step 2: Build the exception queue view**

Create `Operations.vue` as a work-focused table, not nested cards. It must include:

- Segmented status tabs for `待重试`, `死信`, and `待确认发送` with counts.
- Columns for subject/sender, state, attempts, last error, waiting time, and last update.
- A row detail drawer containing inbound identity, delivery identity, and chronological event timeline.
- `重新分析` and `转人工` commands for dead-letter jobs.
- `确认已发送`, `确认未发送并重发`, and `转人工` commands for uncertain deliveries.
- A required reason dialog for every mutation; the resend dialog must state that this creates a new outgoing email.
- Disabled controls while a request is pending, empty/loading/error states, page controls, and mobile horizontal scrolling without text overlap.

Use Element Plus `Warning`, `RefreshRight`, `CircleCheck`, `Promotion`, and `UserFilled` icons rather than text-only tool buttons. Use tags for state, not decorative cards.

- [ ] **Step 3: Add route and sidebar attention count**

Add `/operations` to the authenticated children in `frontend/src/router/index.js`. In `Layout.vue`, add a menu item with `Warning` icon and a badge from `stats.operations.total_attention`; update `activeMenu` for `/operations`. Keep the existing 30-second stats refresh and responsive menu behavior.

- [ ] **Step 4: Build the frontend**

Run: `npm run build`

Working directory: `frontend`

Expected: Vite completes without errors and refreshes `src/email_agent/web/dist`.

- [ ] **Step 5: Update operator documentation**

Update `README.md` to describe UID-based discovery, raw spool retention, job retry/dead-letter behavior, the no-auto-resend rule, the exception center workflow, and the new processing settings. Replace statements that say the system only scans unread mail or simply leaves a draft after every SMTP failure.

- [ ] **Step 6: Run full backend verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q src main.py web_app.py tests
git diff --check
```

Expected: all tests PASS, compileall emits no errors, and `git diff --check` emits no output.

- [ ] **Step 7: Perform local UI verification**

Start the local server against a temporary database populated with one job in each attention state. Verify at desktop `1440x900` and mobile `390x844`:

- The sidebar badge and operations tabs show the same attention total.
- Long subjects, SMTP errors, RFC Message-IDs, and customer addresses wrap or truncate without overlapping controls.
- The detail drawer timeline remains readable on mobile.
- Every mutation requires a reason and refreshes the row/count once.
- Confirm-sent never invokes SMTP; resend shows the explicit warning and creates one replacement delivery.

Capture screenshots for both viewports and inspect browser console/network output for errors.

- [ ] **Step 8: Commit UI and documentation**

```powershell
git add frontend/src/api/operations.js frontend/src/views/Operations.vue frontend/src/router/index.js frontend/src/views/Layout.vue README.md src/email_agent/web/dist
git commit -m "feat: add mail processing exception center"
```

- [ ] **Step 9: Final branch review**

Review the complete branch against `docs/superpowers/specs/2026-09-21-reliable-mail-processing-design.md`. Confirm every production SMTP call is reachable only through `DeliveryService`, every `sending` recovery path ends in `awaiting_confirmation`, and no migration updates or deletes existing `processed_emails` rows. Run `git status --short` and ensure only intentional files remain.
