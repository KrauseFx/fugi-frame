import hmac
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request

from .config import AppConfig
from .indexer import IndexRefresher, LibraryIndex


class FrameoTransport:
    FRAMEO_PACKAGE = "net.frameo.frame"
    FRAMEO_BRIGHTNESS_PREF_KEY = "KEY_DISPLAY_BRIGHTNESS"
    FRAMEO_SETTINGS_GEAR_TAP = (1400, 311)
    FRAMEO_DISPLAY_SETTINGS_TAP = (260, 436)
    FRAMEO_SETTINGS_BACK_TAP = (55, 55)
    FRAMEO_BRIGHTNESS_SLIDER_BOUNDS = (560, 177, 1950, 177)

    def __init__(self, config: AppConfig, logger: logging.Logger):
        self._config = config
        self._logger = logger

    def push_image(self, local_path: str) -> None:
        target_args = self._target_args()
        remote_path = self._config.frameo_remote_path
        if self._config.frameo_delete_all_images_before_push:
            remote_path = self._build_remote_path(local_path)

        if self._config.frameo_device_host:
            self._run(
                [
                    self._config.frameo_adb_path,
                    "connect",
                    f"{self._config.frameo_device_host}:{self._config.frameo_device_port}",
                ],
                allow_failure=True,
            )
        if self._config.frameo_delete_all_images_before_push:
            self._run(target_args + ["push", local_path, remote_path])
            remote_dir = os.path.dirname(remote_path.rstrip("/"))
            remote_name = os.path.basename(remote_path)
            self._run(
                target_args
                + [
                    "shell",
                    (
                        f"find {shlex.quote(remote_dir)} -maxdepth 1 -type f "
                        "\\( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \\) "
                        f"! -name {shlex.quote(remote_name)} -exec rm -f {{}} +"
                    ),
                ],
                allow_failure=True,
            )
        else:
            self._run(
                target_args + ["shell", "rm", "-f", self._config.frameo_remote_path],
                allow_failure=True,
            )
            self._run(target_args + ["push", local_path, remote_path])
        self._run(
            target_args
            + [
                "shell",
                "am",
                "broadcast",
                "-a",
                "android.intent.action.MEDIA_MOUNTED",
                "-d",
                "file:///storage/self/primary",
            ]
        )

    def get_brightness(self) -> int:
        pref = self._read_frameo_brightness_preference()
        if pref is not None:
            return pref

        window_override = self._read_frameo_window_brightness()
        if window_override is not None:
            return window_override

        result = self._shell(["settings", "get", "system", "screen_brightness"])
        raw = result.stdout.strip().splitlines()[-1].strip()
        return self._clamp_brightness(raw)

    def set_brightness(self, raw_brightness: int) -> int:
        raw_brightness = self._clamp_brightness(raw_brightness)
        self.set_display_on(True)
        try:
            self._open_frameo_display_settings()
            self._tap_frameo_brightness_slider(raw_brightness)
            time.sleep(0.35)
            return self.get_brightness()
        finally:
            self._return_to_frameo_slideshow()

    def _clamp_brightness(self, value: object) -> int:
        return max(0, min(255, int(value)))

    def _read_frameo_brightness_preference(self) -> Optional[int]:
        key = self.FRAMEO_BRIGHTNESS_PREF_KEY
        command = (
            f"run-as {self.FRAMEO_PACKAGE} sh -c "
            f"'grep -h {shlex.quote(key)} shared_prefs/*.xml 2>/dev/null' "
            f"|| grep -h {shlex.quote(key)} /data/data/{self.FRAMEO_PACKAGE}/shared_prefs/*.xml 2>/dev/null"
        )
        result = self._shell(["sh", "-c", command], allow_failure=True)
        if result.returncode != 0 and not result.stdout.strip():
            return None
        match = re.search(rf'{re.escape(key)}[^>]*\bvalue="(\d+)"', result.stdout)
        if not match:
            match = re.search(rf'value="(\d+)"[^>]*{re.escape(key)}', result.stdout)
        if not match:
            return None
        return self._clamp_brightness(match.group(1))

    def _read_frameo_window_brightness(self) -> Optional[int]:
        result = self._shell(["dumpsys", "window"], allow_failure=True)
        values = []
        for match in re.finditer(r"\bsbrt=([0-9.]+)", result.stdout):
            try:
                values.append(float(match.group(1)))
            except ValueError:
                continue
        if not values:
            return None
        return self._clamp_brightness(round(values[-1] * 255))

    def _open_frameo_display_settings(self) -> None:
        self._shell(["input", "keyevent", "MENU"], allow_failure=True)
        time.sleep(0.45)
        settings_x, settings_y = self.FRAMEO_SETTINGS_GEAR_TAP
        self._shell(["input", "tap", str(settings_x), str(settings_y)], allow_failure=True)
        time.sleep(0.8)
        display_x, display_y = self.FRAMEO_DISPLAY_SETTINGS_TAP
        self._shell(["input", "tap", str(display_x), str(display_y)], allow_failure=True)
        time.sleep(0.4)

    def _tap_frameo_brightness_slider(self, raw_brightness: int) -> None:
        left, top, right, bottom = self.FRAMEO_BRIGHTNESS_SLIDER_BOUNDS
        x = round(left + (right - left) * (raw_brightness / 255))
        y = round((top + bottom) / 2)
        self._shell(["input", "tap", str(x), str(y)], allow_failure=True)

    def _return_to_frameo_slideshow(self) -> None:
        back_x, back_y = self.FRAMEO_SETTINGS_BACK_TAP
        self._shell(["input", "tap", str(back_x), str(back_y)], allow_failure=True)
        time.sleep(0.5)

    def is_display_on(self) -> bool:
        result = self._shell(["dumpsys", "power"])
        output = result.stdout
        wakefulness = re.search(r"mWakefulness=(\w+)", output)
        if wakefulness:
            return wakefulness.group(1).lower() == "awake"
        display_state = re.search(r"Display Power:\s*state=(\w+)", output)
        if display_state:
            return display_state.group(1).lower() == "on"
        return "mHoldingDisplaySuspendBlocker=true" in output

    def set_display_on(self, on: bool) -> bool:
        if self.is_display_on() == on:
            return on

        self._shell(["input", "keyevent", "224" if on else "223"], allow_failure=True)
        time.sleep(0.5)
        current = self.is_display_on()
        if current != on:
            self._shell(["input", "keyevent", "26"], allow_failure=True)
            time.sleep(0.5)
            current = self.is_display_on()
        return current

    def _connect_if_needed(self) -> None:
        if self._config.frameo_device_host:
            self._run(
                [
                    self._config.frameo_adb_path,
                    "connect",
                    f"{self._config.frameo_device_host}:{self._config.frameo_device_port}",
                ],
                allow_failure=True,
            )

    def _shell(self, args: List[str], allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        self._connect_if_needed()
        return self._run(self._target_args() + ["shell"] + args, allow_failure=allow_failure)

    def _target_args(self) -> List[str]:
        if self._config.frameo_device_serial:
            return [self._config.frameo_adb_path, "-s", self._config.frameo_device_serial]
        if self._config.frameo_device_host:
            return [
                self._config.frameo_adb_path,
                "-s",
                f"{self._config.frameo_device_host}:{self._config.frameo_device_port}",
            ]
        raise RuntimeError("Frameo output requires frameo_device_host or frameo_device_serial")

    def _build_remote_path(self, local_path: str) -> str:
        remote_dir = os.path.dirname(self._config.frameo_remote_path.rstrip("/"))
        extension = os.path.splitext(local_path)[1] or ".jpg"
        unique_name = f"fugi-{time.time_ns()}{extension.lower()}"
        return f"{remote_dir}/{unique_name}"

    def _run(self, cmd: List[str], allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        self._logger.info("$ %s", shlex.join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout.strip():
            self._logger.info(result.stdout.strip())
        if result.stderr.strip():
            self._logger.info(result.stderr.strip())
        if result.returncode != 0 and not allow_failure:
            raise RuntimeError(f"ADB command failed ({result.returncode}): {shlex.join(cmd)}")
        return result


class FrameoController:
    def __init__(
        self,
        config: AppConfig,
        index: LibraryIndex,
        transport: FrameoTransport,
        logger: logging.Logger,
    ):
        self._config = config
        self._index = index
        self._transport = transport
        self._logger = logger
        self._send_lock = threading.Lock()
        self._manual_skip_event = threading.Event()

    def send_next(self, reason: str = "timer") -> Dict[str, Any]:
        with self._send_lock:
            result = self._index.pick_next()
            if result is None:
                raise RuntimeError("No eligible photos found")

            session_index, record = result
            local_path = self._index.ensure_cached(
                record,
                self._config.frameo_target_width,
                self._config.frameo_target_height,
                self._config.frameo_jpeg_quality,
                fit_mode=self._config.frameo_fit_mode,
            )
            self._logger.info(
                "Sending photo uuid=%s session=%s reason=%s path=%s size=%sx%s",
                record.uuid,
                session_index,
                reason,
                local_path,
                self._config.frameo_target_width,
                self._config.frameo_target_height,
            )
            self._transport.push_image(local_path)
            return {
                "ok": True,
                "reason": reason,
                "photo_id": record.uuid,
                "session": session_index,
            }

    def skip_now(self) -> Dict[str, Any]:
        result = self.send_next(reason="manual_skip")
        self._manual_skip_event.set()
        return result

    def get_display_state(self) -> Dict[str, Any]:
        return {"ok": True, "on": self._transport.is_display_on()}

    def set_display_on(self, on: bool) -> Dict[str, Any]:
        return {"ok": True, "on": self._transport.set_display_on(bool(on))}

    def get_brightness(self) -> Dict[str, Any]:
        raw = self._transport.get_brightness()
        return {"ok": True, "brightness": round(raw / 255, 4), "raw": raw}

    def set_brightness(self, brightness: float) -> Dict[str, Any]:
        raw = round(max(0.0, min(1.0, float(brightness))) * 255)
        actual_raw = self._transport.set_brightness(raw)
        return {"ok": True, "brightness": round(actual_raw / 255, 4), "raw": actual_raw}

    def run(self, once: bool = False) -> None:
        while True:
            try:
                self.send_next(reason="timer")
            except Exception:
                self._logger.exception("Frameo send failed")
                if once:
                    raise

            if once:
                return
            self._wait_for_next_timer_send()

    def _wait_for_next_timer_send(self) -> None:
        interval = max(self._config.frameo_send_interval_seconds, 1)
        while self._manual_skip_event.wait(interval):
            self._manual_skip_event.clear()


def build_frameo_control_app(controller: Any, token: str) -> FastAPI:
    app = FastAPI(title="Fugi Frameo Control")

    def authorize(request: Request, query_token: Optional[str]) -> None:
        provided = request.headers.get("x-fugi-frame-token") or query_token
        if not provided:
            raise HTTPException(status_code=401, detail="Missing control token")
        if not token or not hmac.compare_digest(str(provided), str(token)):
            raise HTTPException(status_code=403, detail="Invalid control token")

    @app.get("/api/frameo/status")
    def status():
        return {"ok": True}

    @app.post("/api/frameo/skip")
    def skip(request: Request, token: Optional[str] = None):
        authorize(request, token)
        if hasattr(controller, "skip_now"):
            return controller.skip_now()
        return controller.send_next(reason="manual_skip")

    @app.get("/api/frameo/display")
    def get_display(request: Request, token: Optional[str] = None):
        authorize(request, token)
        return controller.get_display_state()

    @app.post("/api/frameo/display")
    async def set_display(request: Request, token: Optional[str] = None):
        authorize(request, token)
        body = await request.json()
        if "on" not in body or not isinstance(body["on"], bool):
            raise HTTPException(status_code=422, detail="Expected boolean field 'on'")
        return controller.set_display_on(body["on"])

    @app.get("/api/frameo/brightness")
    def get_brightness(request: Request, token: Optional[str] = None):
        authorize(request, token)
        return controller.get_brightness()

    @app.post("/api/frameo/brightness")
    async def set_brightness(request: Request, token: Optional[str] = None):
        authorize(request, token)
        body = await request.json()
        value = body.get("brightness")
        if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
            raise HTTPException(status_code=422, detail="Expected numeric field 'brightness' in range 0..1")
        return controller.set_brightness(float(value))

    return app


def _start_control_server(
    config: AppConfig,
    controller: FrameoController,
    logger: logging.Logger,
) -> Optional[Any]:
    if not config.frameo_control_enabled:
        return None
    if not config.frameo_control_token:
        logger.warning("Frameo control server disabled: frameo_control_token is empty")
        return None

    import uvicorn

    app = build_frameo_control_app(controller, config.frameo_control_token)
    uvicorn_config = uvicorn.Config(
        app,
        host=config.frameo_control_bind,
        port=config.frameo_control_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(uvicorn_config)
    thread = threading.Thread(target=server.run, name="frameo-control-server", daemon=True)
    thread.start()
    logger.info(
        "Frameo control server listening on %s:%s",
        config.frameo_control_bind,
        config.frameo_control_port,
    )
    return server


def run_frameo(config: AppConfig, once: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("fugi_frame")

    index = LibraryIndex(config)
    refresher = IndexRefresher(index, config.index_refresh_minutes)
    transport = FrameoTransport(config, logger)
    controller = FrameoController(config, index, transport, logger)
    control_server = None

    logger.info("Building photo index for Frameo output")
    index.rebuild()
    refresher.start()
    if not once:
        control_server = _start_control_server(config, controller, logger)

    try:
        controller.run(once=once)
    finally:
        if control_server is not None:
            control_server.should_exit = True
        refresher.stop()
