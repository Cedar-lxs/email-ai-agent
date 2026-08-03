"""SQLite 状态库：记录邮件处理、草稿审核和对话历史。"""
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


class EmailDB:
    def __init__(self, db_path: str = "./data/emails.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS processed_emails (
                message_id TEXT PRIMARY KEY,
                subject TEXT,
                sender TEXT,
                received_at TEXT,
                intent TEXT,
                sentiment TEXT,
                status TEXT DEFAULT 'pending',
                draft_text TEXT,
                replied_at TEXT,
                notes TEXT,
                original_body TEXT,
                draft_path TEXT,
                in_reply_to TEXT,
                last_error TEXT,
                retry_count INTEGER DEFAULT 0,
                retrieval_trace TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_email TEXT,
                message_id TEXT,
                role TEXT,
                content TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(processed_emails)")}
        migrations = {
            "original_body": "TEXT", "draft_path": "TEXT", "in_reply_to": "TEXT",
            "last_error": "TEXT", "retry_count": "INTEGER DEFAULT 0",
            "retrieval_trace": "TEXT DEFAULT ''",
        }
        for column, definition in migrations.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE processed_emails ADD COLUMN {column} {definition}")
        self._repair_missing_message_ids()
        self.conn.commit()

    def _repair_missing_message_ids(self):
        """为旧数据中缺失的 Message-ID 生成稳定标识，使其可路由和删除。"""
        rows = self.conn.execute("""
            SELECT rowid, subject, sender, received_at, original_body
            FROM processed_emails
            WHERE message_id IS NULL OR message_id = ''
        """).fetchall()
        for row in rows:
            fingerprint = "\x1f".join(str(row[key] or "") for key in (
                "subject", "sender", "received_at", "original_body"
            ))
            digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
            message_id = f"generated-{digest}@local"
            suffix = 1
            while self.get_email(message_id):
                suffix += 1
                message_id = f"generated-{digest}-{suffix}@local"
            self.conn.execute(
                "UPDATE processed_emails SET message_id=? WHERE rowid=?",
                (message_id, row["rowid"]),
            )
        self.conn.commit()

    def is_processed(self, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM processed_emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None and row["status"] not in {"failed", "retry"}

    def mark_processed(self, message_id: str, subject: str, sender: str,
                       intent: str = "", sentiment: str = "", status: str = "pending",
                       original_body: str = "", in_reply_to: str = ""):
        self.conn.execute("""
            INSERT INTO processed_emails
                (message_id, subject, sender, received_at, intent, sentiment, status,
                 original_body, in_reply_to, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')
            ON CONFLICT(message_id) DO UPDATE SET
                subject=excluded.subject, sender=excluded.sender, intent=excluded.intent,
                sentiment=excluded.sentiment, status=excluded.status,
                original_body=excluded.original_body, in_reply_to=excluded.in_reply_to,
                last_error=''
        """, (message_id, subject, sender, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              intent, sentiment, status, original_body, in_reply_to))
        self.conn.commit()

    def update_status(self, message_id: str, status: str, draft_text: str = "",
                      draft_path: str = "", notes: str = ""):
        replied_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "replied" else None
        self.conn.execute("""
            UPDATE processed_emails SET status=?,
                draft_text=CASE WHEN ? != '' THEN ? ELSE draft_text END,
                draft_path=CASE WHEN ? != '' THEN ? ELSE draft_path END,
                notes=CASE WHEN ? != '' THEN ? ELSE notes END,
                replied_at=? WHERE message_id=?
        """, (status, draft_text, draft_text, draft_path, draft_path, notes, notes,
              replied_at, message_id))
        self.conn.commit()

    def record_error(self, message_id: str, error: str, keep_status: bool = False):
        status_sql = "status=status" if keep_status else "status='failed'"
        self.conn.execute(f"""
            UPDATE processed_emails SET {status_sql}, last_error=?,
                retry_count=COALESCE(retry_count, 0)+1 WHERE message_id=?
        """, (error[:1000], message_id))
        self.conn.commit()

    def mark_failed(self, message_id: str, error: str):
        self.record_error(message_id, error)

    def get_pending_drafts(self):
        return self.get_emails("draft_ready")

    def get_emails(self, status: str = "all", query: str = "", limit: int = 200):
        sql = "SELECT * FROM processed_emails WHERE 1=1"
        params = []
        if status and status != "all":
            sql += " AND status=?"
            params.append(status)
        if query.strip():
            sql += " AND (subject LIKE ? OR sender LIKE ? OR intent LIKE ? OR original_body LIKE ?)"
            pattern = f"%{query.strip()}%"
            params.extend([pattern] * 4)
        sql += " ORDER BY received_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def get_status_counts(self):
        return {
            row["status"]: row["count"]
            for row in self.conn.execute("""
                SELECT status, COUNT(*) AS count FROM processed_emails GROUP BY status
            """).fetchall()
        }

    def get_email(self, message_id: str):
        return self.conn.execute(
            "SELECT * FROM processed_emails WHERE message_id=?", (message_id,)
        ).fetchone()

    def get_stats(self):
        return self.conn.execute("""
            SELECT status, COUNT(*) AS count FROM processed_emails GROUP BY status
            ORDER BY status
        """).fetchall()

    def get_history_for_sender(self, sender_email: str, limit: int = 5):
        return self.conn.execute("""
            SELECT * FROM conversation_history WHERE sender_email = ?
            ORDER BY created_at DESC LIMIT ?
        """, (sender_email, limit)).fetchall()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute("""
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now','localtime'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, value))
        self.conn.commit()

    def delete_emails(self, message_ids: list[str]) -> list[str]:
        ids = list(dict.fromkeys(value for value in message_ids if value))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT message_id, draft_path FROM processed_emails WHERE message_id IN ({placeholders})", ids
        ).fetchall()
        found = [row["message_id"] for row in rows]
        self.conn.execute(
            f"DELETE FROM conversation_history WHERE message_id IN ({placeholders})", ids
        )
        self.conn.execute(f"DELETE FROM processed_emails WHERE message_id IN ({placeholders})", ids)
        self.conn.commit()
        for row in rows:
            path = Path(row["draft_path"] or "")
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
        return found

    def save_retrieval_trace(self, message_id: str, trace: dict):
        self.conn.execute(
            "UPDATE processed_emails SET retrieval_trace=? WHERE message_id=?",
            (json.dumps(trace, ensure_ascii=False), message_id),
        )
        self.conn.commit()

    @staticmethod
    def parse_retrieval_trace(row) -> dict:
        try:
            return json.loads(row["retrieval_trace"] or "{}")
        except (json.JSONDecodeError, KeyError, TypeError):
            return {}

    def save_conversation(self, sender_email: str, message_id: str,
                          role: str, content: str):
        self.conn.execute("""
            INSERT INTO conversation_history (sender_email, message_id, role, content)
            VALUES (?, ?, ?, ?)
        """, (sender_email, message_id, role, content))
        self.conn.commit()
