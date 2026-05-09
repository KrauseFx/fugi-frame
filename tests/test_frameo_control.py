import json
import logging
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig, load_config
from app.frameo_output import FrameoController, FrameoTransport, build_frameo_control_app


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
                        "frameo_control_token": "test",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertTrue(config.frameo_control_enabled)
        self.assertEqual(config.frameo_control_bind, "0.0.0.0")
        self.assertEqual(config.frameo_control_port, 8767)
        self.assertEqual(config.frameo_control_token, "test")

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
        client = TestClient(build_frameo_control_app(controller, token="test"))

        self.assertEqual(client.post("/api/frameo/skip").status_code, 401)
        self.assertEqual(client.post("/api/frameo/skip?token=wrong").status_code, 403)

        response = client.post("/api/frameo/skip?token=test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["photo_id"], "photo-2")
        self.assertEqual(controller.reasons, ["manual_skip"])

    def test_display_endpoint_requires_token_and_sets_target_state(self):
        class StubController:
            def __init__(self):
                self.display_values = []

            def get_display_state(self):
                return {"ok": True, "on": False}

            def set_display_on(self, on):
                self.display_values.append(on)
                return {"ok": True, "on": on}

        controller = StubController()
        client = TestClient(build_frameo_control_app(controller, token="test"))

        self.assertEqual(client.get("/api/frameo/display").status_code, 401)
        self.assertEqual(client.get("/api/frameo/display?token=test").json(), {"ok": True, "on": False})

        response = client.post("/api/frameo/display?token=test", json={"on": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "on": True})
        self.assertEqual(controller.display_values, [True])

    def test_brightness_endpoint_requires_token_and_maps_fractional_value(self):
        class StubController:
            def __init__(self):
                self.brightness_values = []

            def get_brightness(self):
                return {"ok": True, "brightness": 0.5, "raw": 128}

            def set_brightness(self, brightness):
                self.brightness_values.append(brightness)
                return {"ok": True, "brightness": brightness, "raw": 191}

        controller = StubController()
        client = TestClient(build_frameo_control_app(controller, token="test"))

        self.assertEqual(client.get("/api/frameo/brightness").status_code, 401)
        self.assertEqual(
            client.get("/api/frameo/brightness?token=test").json(),
            {"ok": True, "brightness": 0.5, "raw": 128},
        )

        response = client.post("/api/frameo/brightness?token=test", json={"brightness": 0.75})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "brightness": 0.75, "raw": 191})
        self.assertEqual(controller.brightness_values, [0.75])

    def test_brightness_endpoint_rejects_out_of_range_values(self):
        class StubController:
            pass

        client = TestClient(build_frameo_control_app(StubController(), token="test"))

        response = client.post("/api/frameo/brightness?token=test", json={"brightness": 1.5})

        self.assertEqual(response.status_code, 422)

    def test_transport_reads_frameo_internal_brightness_preference_before_android_global_setting(self):
        class StubTransport(FrameoTransport):
            def __init__(self):
                pass

            def _shell(self, args, allow_failure=False):
                command = " ".join(args)
                if "KEY_DISPLAY_BRIGHTNESS" in command:
                    return subprocess_result('<?xml version=\'1.0\' encoding=\'utf-8\' standalone=\'yes\' ?>\n<map><int name="KEY_DISPLAY_BRIGHTNESS" value="86" /></map>')
                if args == ["settings", "get", "system", "screen_brightness"]:
                    return subprocess_result("30\n")
                raise AssertionError(f"unexpected shell command: {args}")

        self.assertEqual(StubTransport().get_brightness(), 86)

    def test_transport_sets_frameo_visible_brightness_through_frameo_settings_ui(self):
        class StubTransport(FrameoTransport):
            def __init__(self):
                self.commands = []
                self.pref_reads = 0

            def is_display_on(self):
                return True

            def _shell(self, args, allow_failure=False):
                self.commands.append(args)
                command = " ".join(args)
                if "KEY_DISPLAY_BRIGHTNESS" in command:
                    self.pref_reads += 1
                    return subprocess_result('<map><int name="KEY_DISPLAY_BRIGHTNESS" value="128" /></map>')
                if args[:2] == ["dumpsys", "window"]:
                    return subprocess_result("sbrt=0.5019608")
                return subprocess_result("")

        transport = StubTransport()

        actual = transport.set_brightness(128)

        self.assertEqual(actual, 128)
        self.assertIn(["input", "keyevent", "MENU"], transport.commands)
        self.assertIn(["input", "tap", "1400", "311"], transport.commands)
        self.assertIn(["input", "tap", "260", "436"], transport.commands)
        self.assertTrue(any(cmd[:2] == ["input", "tap"] for cmd in transport.commands))
        self.assertIn(["input", "tap", "55", "55"], transport.commands)
        self.assertNotIn(["input", "keyevent", "BACK"], transport.commands)
        self.assertNotIn(["am", "start", "-n", "net.frameo.frame/.feature.setting.ui.activity.ASettings"], transport.commands)
        self.assertNotIn(["settings", "put", "system", "screen_brightness", "128"], transport.commands)


def subprocess_result(stdout, returncode=0):
    import subprocess

    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


if __name__ == "__main__":
    unittest.main()
