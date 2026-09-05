"""Structured product facts distilled from the local knowledge directory."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from email_agent.domain.models import MultimodalObservation


@dataclass
class ProductRecord:
    model: str
    family: str = ""
    management_type: str = "unknown"
    ports: list[str] = field(default_factory=list)
    poe: str = ""
    versions: list[str] = field(default_factory=list)
    source: str = ""
    section: str = ""


class ProductIndex:
    def __init__(self, records: list[ProductRecord] | None = None):
        self._records: dict[str, ProductRecord] = {}
        for record in records or []:
            self._merge(record)

    @classmethod
    def from_knowledge(cls, knowledge_dir: Path) -> "ProductIndex":
        index = cls()
        if not knowledge_dir.exists():
            return index
        for path in knowledge_dir.rglob("products.vector.json"):
            index._load_structured(path, knowledge_dir)
        for path in knowledge_dir.rglob("*.md"):
            index._load_markdown(path, knowledge_dir)
        return index

    def find(self, identifiers: list[str]) -> list[ProductRecord]:
        found: list[ProductRecord] = []
        seen: set[str] = set()
        for identifier in identifiers:
            key = _model_key(identifier)
            if key and key in self._records and key not in seen:
                found.append(self._records[key])
                seen.add(key)
        return found

    def _load_structured(self, path: Path, root: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        documents = data.get("documents", data) if isinstance(data, dict) else data
        if not isinstance(documents, list):
            return
        for document in documents:
            if not isinstance(document, dict):
                continue
            metadata = document.get("metadata") or {}
            answer = _key_values(str(document.get("answer", "")))
            model = _first_value(metadata, answer, "model", "型号", "product_model")
            if not model:
                model = _model_from_text(str(document.get("question", "")))
            if not model:
                continue
            ports = _values(metadata, answer, "ports", "port_composition", "端口", "端口组成")
            poe_ports = _first_value(metadata, answer, "poe_ports")
            if poe_ports:
                ports.append(f"{poe_ports} PoE ports")
            self._merge(ProductRecord(
                model=model,
                family=_first_value(metadata, answer, "family", "series", "category", "分类"),
                management_type=_management_type(_first_value(
                    metadata, answer, "management_type", "management", "managed", "管理"
                )),
                ports=ports,
                poe=_first_value(metadata, answer, "poe", "poe_standard", "poe_ports"),
                versions=_values(metadata, answer, "versions", "version", "版本"),
                source=str(path.relative_to(root)),
                section=str(document.get("section", "")),
            ))

    def _load_markdown(self, path: Path, root: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        front_matter = _key_values(_front_matter(text))
        heading_model = _model_from_text(text)
        model = _first_value(front_matter, {}, "model", "型号", "product_model") or heading_model
        if not model:
            return
        body_fields = _key_values(text)
        port_lines = re.findall(r"(?im)^\s*[-*]\s*\*{0,2}[^\n]*?(?:端口|ports?)[^\n]*$", text)
        ports = _values(front_matter, body_fields, "ports", "port_composition", "端口", "端口组成")
        ports.extend(_clean_markdown(line) for line in port_lines)
        self._merge(ProductRecord(
            model=model,
            family=_first_value(front_matter, body_fields, "family", "series", "category", "分类"),
            management_type=_management_type(_first_value(
                front_matter, body_fields, "management_type", "management", "managed", "管理"
            ) or text),
            ports=ports,
            poe=_first_value(front_matter, body_fields, "poe", "poe_standard", "poe_ports"),
            versions=_values(front_matter, body_fields, "versions", "version", "版本"),
            source=str(path.relative_to(root)),
            section=str(path.relative_to(root).parent),
        ))

    def _merge(self, incoming: ProductRecord) -> None:
        key = _model_key(incoming.model)
        if not key:
            return
        existing = self._records.get(key)
        if not existing:
            self._records[key] = ProductRecord(
                model=incoming.model.strip(), family=incoming.family,
                management_type=incoming.management_type, ports=_unique(incoming.ports),
                poe=incoming.poe, versions=_unique(incoming.versions),
                source=incoming.source, section=incoming.section,
            )
            return
        existing.family = existing.family or incoming.family
        if existing.management_type == "unknown":
            existing.management_type = incoming.management_type
        existing.ports = _unique(existing.ports + incoming.ports)
        existing.poe = existing.poe or incoming.poe
        existing.versions = _unique(existing.versions + incoming.versions)
        existing.source = _join_values(existing.source, incoming.source)
        existing.section = _join_values(existing.section, incoming.section)


def format_product_context(records: list[ProductRecord]) -> str:
    if not records:
        return ""
    lines = ["[Structured product facts]"]
    for record in records:
        facts = [f"model: {record.model}"]
        if record.family:
            facts.append(f"family: {record.family}")
        if record.management_type != "unknown":
            facts.append(f"management: {record.management_type}")
        if record.ports:
            facts.append(f"ports: {', '.join(record.ports)}")
        if record.poe:
            facts.append(f"PoE: {record.poe}")
        if record.versions:
            facts.append(f"versions: {', '.join(record.versions)}")
        if record.source:
            facts.append(f"source: {record.source}")
        lines.append("; ".join(facts))
    return "\n".join(lines)


def detect_product_conflicts(records: list[ProductRecord],
                             observation: MultimodalObservation | None) -> list[str]:
    observed = _management_type(getattr(observation, "switch_management_type", "unknown"))
    if observed == "unknown":
        return []
    observed_models = {
        _model_key(value) for value in (
            getattr(observation, "product_identifiers", [])
            + getattr(observation, "model_numbers", [])
        ) if _model_key(value)
    }
    for record in records:
        if (not observed_models or _model_key(record.model) in observed_models) and (
            record.management_type in {"managed", "unmanaged"}
            and record.management_type != observed
        ):
            return ["product_conflict"]
    return []


def _key_values(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(?:[-*]\s*)?\*{0,2}([^:\uff1a*]+?)\*{0,2}\s*[:\uff1a]\s*(.+?)\s*$", line)
        if match:
            fields[match.group(1).strip().lower()] = _clean_markdown(match.group(2))
    return fields


def _front_matter(text: str) -> str:
    match = re.match(r"^---\s*\n(.*?)\n---", text, flags=re.DOTALL)
    return match.group(1) if match else ""


def _first_value(first: dict, second: dict, *names: str) -> str:
    for name in names:
        for fields in (first, second):
            value = fields.get(name.lower()) if isinstance(fields, dict) else None
            if value not in (None, ""):
                return str(value).strip()
    return ""


def _values(first: dict, second: dict, *names: str) -> list[str]:
    value = _first_value(first, second, *names)
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,;/]", value) if item.strip()]


def _management_type(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"false", "unmanaged"} or "非管理" in normalized or "非网管" in normalized:
        return "unmanaged"
    if normalized in {"true", "managed"} or any(term in normalized for term in ("云管理", "网管", "管理型")):
        return "managed"
    return "unknown"


def _model_from_text(text: str) -> str:
    match = re.search(r"(?m)^\s*#\s*([A-Za-z][A-Za-z0-9_-]{1,})", text)
    if match:
        return match.group(1)
    match = re.search(r"(?:产品参数|产品型号)\s*[:\uff1a]?\s*([A-Za-z][A-Za-z0-9_-]{1,})", text)
    return match.group(1) if match else ""


def _model_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _clean_markdown(value: str) -> str:
    return re.sub(r"[*`]+", "", value).strip()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _join_values(first: str, second: str) -> str:
    return "; ".join(_unique([first, second]))
