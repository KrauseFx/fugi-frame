import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_CONFIG_PATH = "config.json"


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


@dataclass
class AppConfig:
    bind: str = "0.0.0.0"
    port: int = 8765
    output_mode: str = "web"  # "web" or "frameo"
    source: str = "apple_photos"  # "apple_photos" or "immich"
    immich_url: str = "http://localhost:2283"
    immich_api_key: str = ""
    camera_make_allowlist: List[str] = field(default_factory=lambda: ["FUJIFILM"])
    camera_model_allowlist: List[str] = field(default_factory=list)
    session_gap_minutes: int = 10
    selection_mode: str = "shuffle"  # "shuffle" or "random"
    avoid_consecutive_sessions: bool = True
    change_interval_seconds: int = 120
    transition_ms: int = 1200
    fit_mode: str = "cover"  # "contain" or "cover"
    max_image_width: int = 1920
    max_image_height: int = 1080
    jpeg_quality: int = 85
    cache_dir: str = "~/.fugi-frame/cache"
    index_refresh_minutes: int = 60
    random_seed: Optional[int] = None
    frameo_device_host: str = ""
    frameo_device_port: int = 5555
    frameo_device_serial: str = ""
    frameo_adb_path: str = "adb"
    frameo_remote_path: str = "/storage/self/primary/DCIM/fugi-current.jpg"
    frameo_target_width: int = 2000
    frameo_target_height: int = 1200
    frameo_fit_mode: str = "cover"
    frameo_jpeg_quality: int = 85
    frameo_send_interval_seconds: int = 120
    frameo_delete_all_images_before_push: bool = False

    @property
    def cache_dir_expanded(self) -> str:
        return _expand(self.cache_dir)



def load_config(path: Optional[str] = None) -> AppConfig:
    config_path = path or _get_env("FUGI_FRAME_CONFIG", "FUJI_FRAME_CONFIG") or DEFAULT_CONFIG_PATH
    defaults = AppConfig()
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        config = AppConfig(
            bind=data.get("bind", defaults.bind),
            port=int(data.get("port", defaults.port)),
            output_mode=data.get("output_mode", defaults.output_mode),
            source=data.get("source", defaults.source),
            immich_url=data.get("immich_url", defaults.immich_url),
            immich_api_key=data.get("immich_api_key", defaults.immich_api_key),
            camera_make_allowlist=data.get("camera_make_allowlist", defaults.camera_make_allowlist),
            camera_model_allowlist=data.get("camera_model_allowlist", defaults.camera_model_allowlist),
            session_gap_minutes=int(data.get("session_gap_minutes", defaults.session_gap_minutes)),
            selection_mode=data.get("selection_mode", defaults.selection_mode),
            avoid_consecutive_sessions=bool(
                data.get("avoid_consecutive_sessions", defaults.avoid_consecutive_sessions)
            ),
            change_interval_seconds=int(
                data.get("change_interval_seconds", defaults.change_interval_seconds)
            ),
            transition_ms=int(data.get("transition_ms", defaults.transition_ms)),
            fit_mode=data.get("fit_mode", defaults.fit_mode),
            max_image_width=int(data.get("max_image_width", defaults.max_image_width)),
            max_image_height=int(data.get("max_image_height", defaults.max_image_height)),
            jpeg_quality=int(data.get("jpeg_quality", defaults.jpeg_quality)),
            cache_dir=data.get("cache_dir", defaults.cache_dir),
            index_refresh_minutes=int(
                data.get("index_refresh_minutes", defaults.index_refresh_minutes)
            ),
            random_seed=data.get("random_seed", defaults.random_seed),
            frameo_device_host=data.get("frameo_device_host", defaults.frameo_device_host),
            frameo_device_port=int(data.get("frameo_device_port", defaults.frameo_device_port)),
            frameo_device_serial=data.get("frameo_device_serial", defaults.frameo_device_serial),
            frameo_adb_path=data.get("frameo_adb_path", defaults.frameo_adb_path),
            frameo_remote_path=data.get("frameo_remote_path", defaults.frameo_remote_path),
            frameo_target_width=int(data.get("frameo_target_width", defaults.frameo_target_width)),
            frameo_target_height=int(data.get("frameo_target_height", defaults.frameo_target_height)),
            frameo_fit_mode=data.get("frameo_fit_mode", defaults.frameo_fit_mode),
            frameo_jpeg_quality=int(data.get("frameo_jpeg_quality", defaults.frameo_jpeg_quality)),
            frameo_send_interval_seconds=int(
                data.get("frameo_send_interval_seconds", defaults.frameo_send_interval_seconds)
            ),
            frameo_delete_all_images_before_push=bool(
                data.get(
                    "frameo_delete_all_images_before_push",
                    defaults.frameo_delete_all_images_before_push,
                )
            ),
        )
    else:
        config = defaults

    env_makes = _parse_env_list("FUGI_FRAME_CAMERA_MAKE", "FUJI_FRAME_CAMERA_MAKE")
    env_models = _parse_env_list("FUGI_FRAME_CAMERA_MODEL", "FUJI_FRAME_CAMERA_MODEL")
    env_source = _get_env("FUGI_FRAME_SOURCE", "FUJI_FRAME_SOURCE")
    env_immich_url = _get_env("FUGI_FRAME_IMMICH_URL", "FUJI_FRAME_IMMICH_URL")
    env_immich_api_key = _get_env("FUGI_FRAME_IMMICH_API_KEY", "FUJI_FRAME_IMMICH_API_KEY")
    env_output_mode = _get_env("FUGI_FRAME_OUTPUT_MODE", "FUJI_FRAME_OUTPUT_MODE")
    env_frameo_device_host = _get_env(
        "FUGI_FRAME_FRAMEO_DEVICE_HOST", "FUJI_FRAME_FRAMEO_DEVICE_HOST"
    )
    env_frameo_device_port = _parse_env_int(
        "FUGI_FRAME_FRAMEO_DEVICE_PORT", "FUJI_FRAME_FRAMEO_DEVICE_PORT"
    )
    env_frameo_device_serial = _get_env(
        "FUGI_FRAME_FRAMEO_DEVICE_SERIAL", "FUJI_FRAME_FRAMEO_DEVICE_SERIAL"
    )
    env_frameo_adb_path = _get_env("FUGI_FRAME_FRAMEO_ADB_PATH", "FUJI_FRAME_FRAMEO_ADB_PATH")
    env_frameo_remote_path = _get_env(
        "FUGI_FRAME_FRAMEO_REMOTE_PATH", "FUJI_FRAME_FRAMEO_REMOTE_PATH"
    )
    env_frameo_target_width = _parse_env_int(
        "FUGI_FRAME_FRAMEO_TARGET_WIDTH", "FUJI_FRAME_FRAMEO_TARGET_WIDTH"
    )
    env_frameo_target_height = _parse_env_int(
        "FUGI_FRAME_FRAMEO_TARGET_HEIGHT", "FUJI_FRAME_FRAMEO_TARGET_HEIGHT"
    )
    env_frameo_fit_mode = _get_env("FUGI_FRAME_FRAMEO_FIT_MODE", "FUJI_FRAME_FRAMEO_FIT_MODE")
    env_frameo_jpeg_quality = _parse_env_int(
        "FUGI_FRAME_FRAMEO_JPEG_QUALITY", "FUJI_FRAME_FRAMEO_JPEG_QUALITY"
    )
    env_frameo_send_interval_seconds = _parse_env_int(
        "FUGI_FRAME_FRAMEO_SEND_INTERVAL_SECONDS",
        "FUJI_FRAME_FRAMEO_SEND_INTERVAL_SECONDS",
    )
    env_frameo_delete_all_images_before_push = _parse_env_bool(
        "FUGI_FRAME_FRAMEO_DELETE_ALL_IMAGES_BEFORE_PUSH",
        "FUJI_FRAME_FRAMEO_DELETE_ALL_IMAGES_BEFORE_PUSH",
    )
    if env_makes is not None:
        config.camera_make_allowlist = env_makes
    if env_models is not None:
        config.camera_model_allowlist = env_models
    if env_output_mode is not None:
        config.output_mode = env_output_mode
    if env_source is not None:
        config.source = env_source
    if env_immich_url is not None:
        config.immich_url = env_immich_url
    if env_immich_api_key is not None:
        config.immich_api_key = env_immich_api_key
    if env_frameo_device_host is not None:
        config.frameo_device_host = env_frameo_device_host
    if env_frameo_device_port is not None:
        config.frameo_device_port = env_frameo_device_port
    if env_frameo_device_serial is not None:
        config.frameo_device_serial = env_frameo_device_serial
    if env_frameo_adb_path is not None:
        config.frameo_adb_path = env_frameo_adb_path
    if env_frameo_remote_path is not None:
        config.frameo_remote_path = env_frameo_remote_path
    if env_frameo_target_width is not None:
        config.frameo_target_width = env_frameo_target_width
    if env_frameo_target_height is not None:
        config.frameo_target_height = env_frameo_target_height
    if env_frameo_fit_mode is not None:
        config.frameo_fit_mode = env_frameo_fit_mode
    if env_frameo_jpeg_quality is not None:
        config.frameo_jpeg_quality = env_frameo_jpeg_quality
    if env_frameo_send_interval_seconds is not None:
        config.frameo_send_interval_seconds = env_frameo_send_interval_seconds
    if env_frameo_delete_all_images_before_push is not None:
        config.frameo_delete_all_images_before_push = env_frameo_delete_all_images_before_push

    return config


def _get_env(*keys: str) -> Optional[str]:
    for key in keys:
        raw = os.environ.get(key)
        if raw is not None:
            return raw
    return None


def _parse_env_list(*keys: str) -> Optional[List[str]]:
    raw = _get_env(*keys)
    if raw is None:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items


def _parse_env_int(*keys: str) -> Optional[int]:
    raw = _get_env(*keys)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _parse_env_bool(*keys: str) -> Optional[bool]:
    raw = _get_env(*keys)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for environment variable: {raw}")
