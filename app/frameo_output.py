import logging
import os
import shlex
import subprocess
import time
from typing import List, Optional

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


def run_frameo(config: AppConfig, once: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("fugi_frame")

    index = LibraryIndex(config)
    refresher = IndexRefresher(index, config.index_refresh_minutes)
    transport = FrameoTransport(config, logger)

    logger.info("Building photo index for Frameo output")
    index.rebuild()
    refresher.start()

    try:
        while True:
            try:
                result = index.pick_next()
                if result is None:
                    raise RuntimeError("No eligible photos found")

                session_index, record = result
                local_path = index.ensure_cached(
                    record,
                    config.frameo_target_width,
                    config.frameo_target_height,
                    config.frameo_jpeg_quality,
                    fit_mode=config.frameo_fit_mode,
                )
                logger.info(
                    "Sending photo uuid=%s session=%s path=%s size=%sx%s",
                    record.uuid,
                    session_index,
                    local_path,
                    config.frameo_target_width,
                    config.frameo_target_height,
                )
                transport.push_image(local_path)
            except Exception:
                logger.exception("Frameo send failed")
                if once:
                    raise

            if once:
                return
            time.sleep(max(config.frameo_send_interval_seconds, 1))
    finally:
        refresher.stop()
