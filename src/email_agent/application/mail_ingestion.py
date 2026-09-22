"""Durably ingest IMAP messages by UID before business processing."""
import uuid

from email_agent.domain.models import IngestionResult
from email_agent.infrastructure.mail_fetcher import MailFetcher


class MailIngestionService:
    def __init__(self, store, spool, account: str, folder: str = "INBOX",
                 batch_size: int = 20):
        self.store = store
        self.spool = spool
        self.account = account
        self.folder = folder
        self.batch_size = max(1, int(batch_size))

    def ingest(self, fetcher) -> IngestionResult:
        uid_validity = str(fetcher.select_folder(self.folder))
        if not uid_validity.isdigit():
            raise RuntimeError("IMAP 未返回有效 UIDVALIDITY")

        cursor = self.store.get_cursor(self.account, self.folder)
        if cursor and cursor.uid_validity != uid_validity:
            self.store.reset_cursor(self.account, self.folder, uid_validity)
            after_uid = 0
        else:
            after_uid = cursor.last_scanned_uid if cursor else 0

        discovered = []
        scanned = []
        uids = sorted(int(uid) for uid in fetcher.search_uids(after_uid))
        for uid in uids[:self.batch_size]:
            raw = fetcher.fetch_uid(uid)
            job_id = uuid.uuid4().hex
            stored = self.spool.store(job_id, raw)
            message_id = MailFetcher.extract_message_id(raw, stored.sha256)
            row, created = self.store.record_discovery(
                job_id=job_id,
                account=self.account,
                folder=self.folder,
                uid_validity=uid_validity,
                imap_uid=uid,
                message_id=message_id,
                raw_path=stored.path,
                raw_sha256=stored.sha256,
            )
            if created:
                discovered.append(row["id"])
            else:
                self.spool.discard(stored.path)
            scanned.append(uid)
        return IngestionResult(tuple(discovered), tuple(scanned), uid_validity)
