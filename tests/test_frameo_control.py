import json
import logging
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig, load_config
from app.frameo_output import FrameoController, build_frameo_control_app


@dataclass(frozen=True)
class FakeRecord:
    uuid: str = "photo-1"


class FakeIndex:
    def __init__(self):
        self.pick_calls = 0
        self.cache_calls = []

    def pick_next(self):
        self.pick_calls += 1
        return 7, FakeRecord()

    def ensure_cached(self, record, width, height, quality, fit_mode):
        self.cache_calls.append((record.uuid, width, height, quality, fit_mode))
        return "/tmp/fugi-photo.jpg"


class FakeTransport:
    def __init__(self):
        self.pushed_paths = []

    def push_image(self, local_path):
        self.pushed_paths.append(local_path)


class FrameoControlTests(unittest.TestCase):
    def test_config_loads_frameo_control_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "frameo_control_enabled": True,
                        "frameo_control_bind": "0.0.0.0",
                        "frameo_control_port": 8767,
                        "frameo_control_token": "local-secret",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertTrue(config.frameo_control_enabled)
        self.assertEqual(config.frameo_control_bind, "0.0.0.0")
        self.assertEqual(config.frameo_control_port, 8767)
        self.assertEqual(config.frameo_control_token, "local-secret")

    def test_controller_send_next_pushes_next_cached_photo(self):
        index = FakeIndex()
        transport = FakeTransport()
        config = AppConfig(
            frameo_target_width=2000,
            frameo_target_height=1200,
            frameo_jpeg_quality=86,
            frameo_fit_mode="cover",
        )
        controller = FrameoController(config, index, transport, logging.getLogger("test"))

        result = controller.send_next(reason="manual_skip")

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["reason"], "manual_skip")
        self.assertEqual(result["photo_id"], "photo-1")
        self.assertEqual(result["session"], 7)
        self.assertEqual(index.pick_calls, 1)
        self.assertEqual(index.cache_calls, [("photo-1", 2000, 1200, 86, "cover")])
        self.assertEqual(transport.pushed_paths, ["/tmp/fugi-photo.jpg"])

    def test_skip_endpoint_requires_token_and_triggers_manual_skip(self):
        class StubController:
            def __init__(self):
                self.reasons = []

            def send_next(self, reason):
                self.reasons.append(reason)
                return {"ok": True, "reason": reason, "photo_id": "photo-2", "session": 8}

        controller = StubController()
        client = TestClient(build_frameo_control_app(controller, token="local-secret"))

        self.assertEqual(client.post("/api/frameo/skip").status_code, 401)
        self.assertEqual(client.post("/api/frameo/skip?token=wrong").status_code, 403)

        response = client.post("/api/frameo/skip?token=local-secret")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["photo_id"], "photo-2")
        self.assertEqual(controller.reasons, ["manual_skip"])


if __name__ == "__main__":
    unittest.main()
