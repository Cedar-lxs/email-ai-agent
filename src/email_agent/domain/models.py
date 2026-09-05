"""领域数据模型。"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmailMedia:
    media_id: str
    filename: str
    content_type: str
    source: str
    content_id: str = ""
    size_bytes: int = 0
    data: bytes = b""
    metadata: dict[str, Any] = field(default_factory=dict)


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
    media: list[EmailMedia] = field(default_factory=list)


@dataclass(frozen=True)
class StoredMedia:
    media_id: str
    filename: str
    content_type: str
    source: str
    path: str
    size_bytes: int
    content_id: str = ""
    derived_from: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MultimodalObservation:
    summary: str = ""
    visible_text: list[str] = field(default_factory=list)
    product_identifiers: list[str] = field(default_factory=list)
    model_numbers: list[str] = field(default_factory=list)
    device_ids: list[str] = field(default_factory=list)
    port_composition: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    switch_management_type: str = "unknown"
    label_text: list[str] = field(default_factory=list)
    fault_signals: list[str] = field(default_factory=list)
    connection_state: list[str] = field(default_factory=list)
    indicator_state: list[str] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    needed_information: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_items: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


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
