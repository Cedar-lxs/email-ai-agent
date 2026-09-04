"""DeepSeek vision-based multimodal analysis for email media."""
import base64
import os
import re
from pathlib import Path

import httpx

from email_agent.domain.models import MultimodalObservation, StoredMedia
from email_agent.infrastructure.llm import AIProcessor


MULTIMODAL_PROMPT = """You are a technical after-sales vision analyst.
Analyze the customer email and attached/inline images. The images may show
network switches, PoE equipment, ports, LEDs, labels, wiring, screenshots, or
physical damage.

Rules:
1. Only describe what is visible or strongly implied by the images.
2. Do not invent product specifications, compatibility, repair steps, or causes.
3. Treat blurry or incomplete images as low confidence.
4. Read switch device labels carefully when visible. Extract the model number,
   device ID or serial-like ID, port composition, Ver/version text, and all
   label OCR text. Identify whether the label says the switch is managed or
   unmanaged. Use switch_management_type exactly as one of: managed,
   unmanaged, unknown.
5. Flag any safety or high-risk signals, including smoke, burnt marks, water
   damage, exposed wiring, disassembly, unsafe power cabling, firmware flashing,
   factory reset, data deletion, voltage changes, or PoE power modification.
6. Return one JSON object only, with these keys:
   summary, visible_text, product_identifiers, model_numbers, device_ids,
   port_composition, versions, switch_management_type, label_text, fault_signals,
   connection_state, indicator_state, risk_signals, needed_information,
   confidence.

Email subject: {subject}
Email body:
{body}
"""


def format_multimodal_context(observation: MultimodalObservation | None) -> str:
    if not observation:
        return ""
    parts = [
        "[Visual observation]",
        f"Summary: {observation.summary or '(none)'}",
        f"Visible text: {', '.join(observation.visible_text) or '(none)'}",
        f"Product identifiers: {', '.join(observation.product_identifiers) or '(none)'}",
        f"Model numbers: {', '.join(getattr(observation, 'model_numbers', []) or []) or '(none)'}",
        f"Device IDs: {', '.join(getattr(observation, 'device_ids', []) or []) or '(none)'}",
        f"Port composition: {', '.join(getattr(observation, 'port_composition', []) or []) or '(none)'}",
        f"Versions: {', '.join(getattr(observation, 'versions', []) or []) or '(none)'}",
        f"Switch management type: {getattr(observation, 'switch_management_type', 'unknown') or 'unknown'}",
        f"Label text: {', '.join(getattr(observation, 'label_text', []) or []) or '(none)'}",
        f"Fault signals: {', '.join(observation.fault_signals) or '(none)'}",
        f"Connection state: {', '.join(observation.connection_state) or '(none)'}",
        f"Indicator state: {', '.join(observation.indicator_state) or '(none)'}",
        f"Risk signals: {', '.join(observation.risk_signals) or '(none)'}",
        f"Needed information: {', '.join(observation.needed_information) or '(none)'}",
        f"Confidence: {observation.confidence:.2f}",
    ]
    if observation.errors:
        parts.append(f"Analysis errors: {', '.join(observation.errors)}")
    return "\n".join(parts)


class MultimodalAnalyzer:
    IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    def __init__(self, config: dict):
        multimodal = config.get("multimodal", {})
        ai = config.get("ai", {})
        self.enabled = bool(multimodal.get("enabled", False))
        self.api_key = (
            os.getenv("MULTIMODAL_API_KEY", "").strip()
            or str(multimodal.get("api_key", "")).strip()
            or str(ai.get("api_key", "")).strip()
        )
        self.api_base = str(
            multimodal.get("api_base") or ai.get("api_base") or "https://api.deepseek.com"
        ).rstrip("/")
        self.model = str(multimodal.get("model", "deepseek-v4-flash-vision-exp"))
        self.timeout = float(multimodal.get("analysis_timeout", 90))
        self.max_images = int(multimodal.get("max_images_per_request", multimodal.get("max_media_per_email", 8)))

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def analyze(self, subject: str, body: str,
                media: list[StoredMedia]) -> MultimodalObservation:
        images = self._image_media(media)
        if not images:
            return MultimodalObservation()
        if not self.available:
            return MultimodalObservation(errors=["多模态分析未启用或缺少 API Key"])
        try:
            response = httpx.post(
                f"{self.api_base}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=self._payload(subject, body, images),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return self._observation_from_response(response.json(), images)
        except Exception as exc:
            return MultimodalObservation(errors=[f"多模态分析失败：{exc}"])

    async def analyze_async(self, subject: str, body: str,
                            media: list[StoredMedia]) -> MultimodalObservation:
        images = self._image_media(media)
        if not images:
            return MultimodalObservation()
        if not self.available:
            return MultimodalObservation(errors=["多模态分析未启用或缺少 API Key"])
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._payload(subject, body, images),
                    timeout=self.timeout,
                )
            response.raise_for_status()
            return self._observation_from_response(response.json(), images)
        except Exception as exc:
            return MultimodalObservation(errors=[f"多模态分析失败：{exc}"])

    def _payload(self, subject: str, body: str, images: list[StoredMedia]) -> dict:
        content = [{
            "type": "text",
            "text": MULTIMODAL_PROMPT.format(subject=subject[:500], body=body[:3000]),
        }]
        for item in images[:self.max_images]:
            content.append({
                "type": "image_url",
                "image_url": {"url": self._data_url(item)},
            })
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You analyze technical support images and return strict JSON."},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
        }

    def _observation_from_response(self, payload: dict,
                                   images: list[StoredMedia]) -> MultimodalObservation:
        message = payload["choices"][0]["message"]
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        data = {}
        if content.strip():
            try:
                data = AIProcessor._parse_json_object(content)
            except ValueError:
                if reasoning.strip():
                    data = self._data_from_reasoning(reasoning)
                else:
                    raise
        elif reasoning.strip():
            data = self._data_from_reasoning(reasoning)
        return self._observation_from_data(data, images)

    def _data_from_reasoning(self, reasoning: str) -> dict:
        try:
            return AIProcessor._parse_json_object(reasoning)
        except ValueError:
            return self._label_fields_from_text(reasoning)

    def _observation_from_data(self, data: dict,
                               images: list[StoredMedia]) -> MultimodalObservation:
        confidence = self._confidence(data.get("confidence", 0))
        if not confidence and self._has_label_fields(data):
            confidence = 0.85
        return MultimodalObservation(
            summary=str(data.get("summary", "")).strip() or self._summary_from_label_data(data),
            visible_text=self._list(data.get("visible_text")),
            product_identifiers=self._list(data.get("product_identifiers")),
            model_numbers=self._list(data.get("model_numbers")),
            device_ids=self._list(data.get("device_ids")),
            port_composition=self._list(data.get("port_composition")),
            versions=self._list(data.get("versions")),
            switch_management_type=self._switch_management_type(
                data.get("switch_management_type")
            ),
            label_text=self._list(data.get("label_text")),
            fault_signals=self._list(data.get("fault_signals")),
            connection_state=self._list(data.get("connection_state")),
            indicator_state=self._list(data.get("indicator_state")),
            risk_signals=self._list(data.get("risk_signals")),
            needed_information=self._list(data.get("needed_information")),
            confidence=confidence,
            raw_items=[{"media_id": item.media_id, "filename": item.filename,
                        "source": item.source, "derived_from": item.derived_from}
                       for item in images],
        )

    def _image_media(self, media: list[StoredMedia]) -> list[StoredMedia]:
        return [item for item in media if item.content_type in self.IMAGE_TYPES and Path(item.path).is_file()]

    @staticmethod
    def _data_url(item: StoredMedia) -> str:
        data = base64.b64encode(Path(item.path).read_bytes()).decode("ascii")
        return f"data:{item.content_type};base64,{data}"

    @staticmethod
    def _list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            items = value
        else:
            items = [value]
        return [str(item).strip() for item in items if str(item).strip()]

    @staticmethod
    def _confidence(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))

    @staticmethod
    def _switch_management_type(value) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return "unknown"
        unmanaged_terms = ("unmanaged", "non-managed", "非管理", "傻瓜")
        managed_terms = ("managed", "web managed", "smart managed", "管理型", "网管")
        if any(term in normalized for term in unmanaged_terms):
            return "unmanaged"
        if any(term in normalized for term in managed_terms):
            return "managed"
        return "unknown"

    @classmethod
    def _label_fields_from_text(cls, text: str) -> dict:
        return {
            "model_numbers": cls._unique(cls._matches(
                text, r"\bModel\s*:\s*([A-Z0-9][A-Z0-9._-]{2,})"
            )),
            "device_ids": cls._unique(
                cls._matches(text, r"\bDevice\s*ID\s*:\s*([A-Z0-9][A-Z0-9._-]{8,})")
                + cls._matches(text, r"\bBarcode\s*(?:text|number)?\s*:\s*([A-Z0-9][A-Z0-9._-]{8,})")
            ),
            "port_composition": cls._unique(cls._matches(
                text, r"\bPort\s*:\s*([A-Z0-9*.+/\s-]+(?:Uplink|uplink))"
            )),
            "versions": cls._unique(cls._matches(
                text, r"\bVer(?:sion)?\s*:?\s*([A-Z]?\d+(?:\.\d+)*)"
            )),
            "switch_management_type": cls._management_type_from_text(text),
            "label_text": cls._unique(cls._matches(
                text, r"(Cloud-Managed\s+Gigabit\s+POE\s+Switch|Managed\s+Switch|Unmanaged\s+Switch|非管理型交换机|管理型交换机)"
            )),
            "visible_text": cls._visible_label_lines(text),
            "confidence": cls._confidence_from_text(text),
        }

    @staticmethod
    def _matches(text: str, pattern: str) -> list[str]:
        values = []
        for match in re.findall(pattern, str(text or ""), flags=re.I):
            value = str(match or "").strip().strip('".,;')
            if '"' in value:
                value = value.split('"', 1)[0].strip()
            if "This is" in value:
                value = value.split("This is", 1)[0].strip()
            if value:
                values.append(value.strip('".,;'))
        return values

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    @classmethod
    def _management_type_from_text(cls, text: str) -> str:
        lowered = str(text or "").lower()
        if any(term in lowered for term in ("unmanaged", "non-managed", "非管理", "傻瓜")):
            return "unmanaged"
        if any(term in lowered for term in ("cloud-managed", "web managed", "smart managed", "managed switch", "管理型", "网管")):
            return "managed"
        return "unknown"

    @classmethod
    def _visible_label_lines(cls, text: str) -> list[str]:
        lines = []
        for line in str(text or "").splitlines():
            stripped = line.strip(" -")
            if any(term in stripped.lower() for term in (
                "model:", "device id:", "port:", "ver:", "managed", "switch",
            )):
                lines.append(stripped.strip('"'))
        return cls._unique(lines)

    @classmethod
    def _summary_from_label_data(cls, data: dict) -> str:
        model = ", ".join(cls._list(data.get("model_numbers")))
        management_type = cls._switch_management_type(data.get("switch_management_type"))
        parts = []
        if model:
            parts.append(f"label shows model {model}")
        if management_type != "unknown":
            parts.append(f"{management_type} switch")
        return "; ".join(parts)

    @classmethod
    def _confidence_from_text(cls, text: str) -> float:
        matches = re.findall(r"\b0\.\d+|1\.0\b", str(text or ""))
        if matches:
            return cls._confidence(matches[-1])
        if any(term in str(text or "").lower() for term in ("clear", "high", "confident")):
            return 0.85
        return 0.0

    @classmethod
    def _has_label_fields(cls, data: dict) -> bool:
        return any(
            cls._list(data.get(field))
            for field in ("model_numbers", "device_ids", "port_composition", "versions", "label_text")
        ) or cls._switch_management_type(data.get("switch_management_type")) != "unknown"
