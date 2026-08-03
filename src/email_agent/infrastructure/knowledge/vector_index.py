"""SQLite 增量知识向量索引。"""
import array
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from email_agent.domain.models import KnowledgeChunk


class SQLiteVectorIndex:
    def __init__(self, path, embedding_client):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_client = embedding_client
        self.conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS vectors (
                chunk_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                source TEXT NOT NULL, section TEXT NOT NULL, content TEXT NOT NULL,
                identifiers_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
                model TEXT NOT NULL, dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vectors_hash ON vectors(content_hash, model);
            CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.conn.commit()

    def sync(self, chunks: list[KnowledgeChunk]) -> dict:
        model = self.embedding_client.model
        existing = {row["chunk_id"]: row for row in self.conn.execute(
            "SELECT chunk_id, content_hash, model FROM vectors"
        )}
        pending = [chunk for chunk in chunks if chunk.chunk_id not in existing or
                   existing[chunk.chunk_id]["content_hash"] != chunk.content_hash or
                   existing[chunk.chunk_id]["model"] != model]
        vectors = self.embedding_client.embed([self._embedding_text(chunk) for chunk in pending])
        keep = {chunk.chunk_id for chunk in chunks}
        with self.conn:
            for chunk, vector in zip(pending, vectors):
                packed = array.array("f", vector).tobytes()
                self.conn.execute("""
                    INSERT OR REPLACE INTO vectors
                        (chunk_id, content_hash, source, section, content, identifiers_json,
                         metadata_json, model, dimensions, vector, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (chunk.chunk_id, chunk.content_hash, chunk.source, chunk.section,
                      chunk.content, json.dumps(chunk.identifiers, ensure_ascii=False),
                      json.dumps(chunk.metadata, ensure_ascii=False), model, len(vector), packed,
                      datetime.now().isoformat(timespec="seconds")))
            stale = [chunk_id for chunk_id in existing if chunk_id not in keep]
            if stale:
                placeholders = ",".join("?" for _ in stale)
                self.conn.execute(f"DELETE FROM vectors WHERE chunk_id IN ({placeholders})", stale)
            self._set_meta("last_success", datetime.now().isoformat(timespec="seconds"))
            self._set_meta("model", model)
        return {"total": len(chunks), "embedded": len(pending),
                "reused": len(chunks) - len(pending), "deleted": len(stale)}

    def search(self, query_vector: list[float], limit: int = 20) -> list[tuple[float, dict]]:
        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        scored = []
        for row in self.conn.execute("SELECT * FROM vectors WHERE model=?",
                                     (self.embedding_client.model,)):
            values = array.array("f")
            values.frombytes(row["vector"])
            if len(values) != len(query_vector):
                continue
            norm = math.sqrt(sum(value * value for value in values)) or 1.0
            score = sum(a * b for a, b in zip(query_vector, values)) / (query_norm * norm)
            scored.append((score, {"chunk_id": row["chunk_id"], "content": row["content"],
                                   "source": row["source"], "section": row["section"],
                                   "identifiers": json.loads(row["identifiers_json"]),
                                   "metadata": json.loads(row["metadata_json"])}))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:limit]

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM vectors WHERE model=?", (self.embedding_client.model,)
        ).fetchone()[0]

    def status(self) -> dict:
        return {"vectors": self.count(), "last_success": self._get_meta("last_success"),
                "model": self.embedding_client.model, "path": str(self.path)}

    def _set_meta(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES (?,?)", (key, value))

    def _get_meta(self, key: str) -> str:
        row = self.conn.execute("SELECT value FROM index_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

    @staticmethod
    def _embedding_text(chunk: KnowledgeChunk) -> str:
        return f"来源：{chunk.source}\n章节：{chunk.section}\n{chunk.content}"
