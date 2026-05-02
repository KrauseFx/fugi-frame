import hmac
import logging
import os
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request

from .config import AppConfig
from .indexer import IndexRefresher, LibraryIndex


class FrameoTransport:
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
