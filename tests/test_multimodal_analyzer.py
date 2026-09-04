import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.models import StoredMedia
from email_agent.infrastructure.multimodal import (
    MultimodalAnalyzer,
    format_multimodal_context,
)


class MultimodalAnalyzerTests(unittest.TestCase):
    def stored_image(self, root: Path) -> StoredMedia:
        path = root / "fault.jpg"
        path.write_bytes(b"fake-image")
        return StoredMedia(
            media_id="m1",
            filename="fault.jpg",
            content_type="image/jpeg",
            source="attachment",
            path=str(path),
            size_bytes=10,
            metadata={"kind": "original"},
        )

    def response(self, content: str):
        return SimpleNamespace(
            raise_for_status=Mock(),
            json=Mock(return_value={"choices": [{"message": {"content": content}}]}),
        )

    def test_request_payload_uses_openai_compatible_image_blocks(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {
                    "enabled": True,
                    "api_key": "vision-key",
                    "api_base": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash-vision-exp",
                },
            })
            payload = '{"summary":"Port light is off","visible_text":["GS105"],"product_identifiers":["GS105"],"fault_signals":["port light off"],"risk_signals":[],"confidence":0.8}'

            with patch(
                "email_agent.infrastructure.multimodal.httpx.post",
                return_value=self.response(payload),
            ) as post:
                analyzer.analyze("Port issue", "The port is not working", [image])

            url = post.call_args.args[0]
            body = post.call_args.kwargs["json"]
            content = body["messages"][1]["content"]
            self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
            self.assertEqual(body["model"], "deepseek-v4-flash-vision-exp")
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_normalizes_json_response(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })
            payload = '{"summary":"The label shows GS105.","visible_text":"GS105","product_identifiers":"GS105","model_numbers":"GS105","device_ids":"ID-7788","port_composition":"5 x RJ45 Gigabit Ethernet","versions":"Ver: 2.1","switch_management_type":"unmanaged","label_text":"Unmanaged Switch GS105 Ver: 2.1","fault_signals":["offline"],"connection_state":"connected","indicator_state":"off","risk_signals":[],"needed_information":"firmware version","confidence":1.5}'

            with patch(
                "email_agent.infrastructure.multimodal.httpx.post",
                return_value=self.response(payload),
            ):
                observation = analyzer.analyze("Offline", "Device offline", [image])

            self.assertEqual(observation.summary, "The label shows GS105.")
            self.assertEqual(observation.visible_text, ["GS105"])
            self.assertEqual(observation.product_identifiers, ["GS105"])
            self.assertEqual(observation.model_numbers, ["GS105"])
            self.assertEqual(observation.device_ids, ["ID-7788"])
            self.assertEqual(observation.port_composition, ["5 x RJ45 Gigabit Ethernet"])
            self.assertEqual(observation.versions, ["Ver: 2.1"])
            self.assertEqual(observation.switch_management_type, "unmanaged")
            self.assertEqual(observation.label_text, ["Unmanaged Switch GS105 Ver: 2.1"])
            self.assertEqual(observation.connection_state, ["connected"])
            self.assertEqual(observation.indicator_state, ["off"])
            self.assertEqual(observation.needed_information, ["firmware version"])
            self.assertEqual(observation.confidence, 1.0)
            self.assertEqual(observation.errors, [])

    def test_extracts_label_fields_from_reasoning_content_when_content_is_empty(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })
            response = {
                "choices": [{
                    "message": {
                        "content": "",
                        "reasoning_content": (
                            'Header: "Cloud-Managed Gigabit POE Switch"\n'
                            "Model: GPS208\n"
                            "Port: 8*1000Mbps.POE + 2*1000Mbps.Uplink\n"
                            "Device ID: GPS208262200F7DZXAX6DL0CO\n"
                            "Barcode text: GPS20826052504675\n"
                            "Ver:V3\n"
                            "confidence should be high, say 0.98"
                        ),
                    },
                }],
            }

            observation = analyzer._observation_from_response(response, [image])

            self.assertEqual(observation.model_numbers, ["GPS208"])
            self.assertIn("GPS208262200F7DZXAX6DL0CO", observation.device_ids)
            self.assertEqual(
                observation.port_composition,
                ["8*1000Mbps.POE + 2*1000Mbps.Uplink"],
            )
            self.assertEqual(observation.versions, ["V3"])
            self.assertEqual(observation.switch_management_type, "managed")
            self.assertGreaterEqual(observation.confidence, 0.9)

    def test_falls_back_to_reasoning_when_content_is_not_json(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })
            response = {
                "choices": [{
                    "message": {
                        "content": "I can read the label clearly.",
                        "reasoning_content": (
                            "Cloud-Managed Gigabit POE Switch\n"
                            "Model: GPS208\n"
                            "Port: 8*1000Mbps.POE + 2*1000Mbps.Uplink\n"
                            "Device ID: GPS208262200F7DZXAX6DL0CO\n"
                            "Ver:V3\n"
                        ),
                    },
                }],
            }

            observation = analyzer._observation_from_response(response, [image])

            self.assertEqual(observation.model_numbers, ["GPS208"])
            self.assertEqual(observation.switch_management_type, "managed")
            self.assertEqual(observation.confidence, 0.85)

    def test_payload_requests_switch_label_fields(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })

            body = analyzer._payload("Label photo", "Please check the switch", [image])
            prompt_text = body["messages"][1]["content"][0]["text"]

            self.assertIn("model_numbers", prompt_text)
            self.assertIn("device_ids", prompt_text)
            self.assertIn("port_composition", prompt_text)
            self.assertIn("versions", prompt_text)
            self.assertIn("switch_management_type", prompt_text)

    def test_api_failure_returns_observation_error(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })

            with patch(
                "email_agent.infrastructure.multimodal.httpx.post",
                side_effect=RuntimeError("network down"),
            ):
                observation = analyzer.analyze("Offline", "Device offline", [image])

            self.assertEqual(observation.summary, "")
            self.assertTrue(any("network down" in error for error in observation.errors))

    def test_async_analyze_uses_same_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })
            payload = '{"summary":"Image is blurry.","visible_text":[],"product_identifiers":[],"fault_signals":[],"risk_signals":["blurry"],"confidence":0.4}'

            client = AsyncMock()
            client.__aenter__.return_value = client
            client.post.return_value = self.response(payload)
            with patch("email_agent.infrastructure.multimodal.httpx.AsyncClient", return_value=client):
                observation = asyncio.run(analyzer.analyze_async("Image", "See photo", [image]))

            self.assertEqual(observation.summary, "Image is blurry.")
            self.assertEqual(observation.risk_signals, ["blurry"])
            self.assertEqual(observation.confidence, 0.4)

    def test_formats_multimodal_context(self):
        context = format_multimodal_context(
            SimpleNamespace(
                summary="Port LED appears off.",
                visible_text=["GS105"],
                product_identifiers=["GS105"],
                model_numbers=["GS105"],
                device_ids=["ID-7788"],
                port_composition=["5 x RJ45 Gigabit Ethernet"],
                versions=["Ver: 2.1"],
                switch_management_type="unmanaged",
                label_text=["Unmanaged Switch"],
                fault_signals=["port LED off"],
                connection_state=[],
                indicator_state=["off"],
                risk_signals=[],
                needed_information=["exact power adapter"],
                confidence=0.82,
                errors=[],
            )
        )

        self.assertIn("[Visual observation]", context)
        self.assertIn("GS105", context)
        self.assertIn("unmanaged", context)
        self.assertIn("ID-7788", context)
        self.assertIn("5 x RJ45 Gigabit Ethernet", context)
        self.assertIn("0.82", context)


if __name__ == "__main__":
    unittest.main()
