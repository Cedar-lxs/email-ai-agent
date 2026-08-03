"""领域数据模型。"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedEmail:
    message_id: str
    subject: str
    sender: str
    sender_name: str
    body_text: str
    body_html: str
    received_at: str
    in_reply_to: str = ""
    imap_uid: str = ""


@dataclass
class IntentResult:
    intent: str
    sentiment: str
    urgency: str
    summary: str
    keywords: list[str]
    needs_human: bool
    source_language: str = "unknown"


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    subject: str = ""
    summary: str = ""
    intent: str = ""
    keywords: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()

    @property
    def combined_text(self) -> str:
        parts = [self.subject, self.summary, " ".join(self.keywords), self.text]
        return "\n".join(part.strip() for part in parts if part and part.strip())


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    content: str
    source: str
    section: str
    content_hash: str
    identifiers: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalTrace:
    mode: str
    query: str
    hits: tuple[dict[str, Any], ...]
    degraded_reason: str = ""


@dataclass(frozen=True)
class KnowledgeHit:
    content: str
    source: str
    section: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexStats:
    entries: int
    sources: int
    errors: tuple[str, ...] = ()
