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
    cache_dir: str = "~/.fuji-frame/cache"
    index_refresh_minutes: int = 60
    random_seed: Optional[int] = None

    @property
    def cache_dir_expanded(self) -> str:
        return _expand(self.cache_dir)



def load_config(path: Optional[str] = None) -> AppConfig:
    config_path = path or os.environ.get("FUJI_FRAME_CONFIG") or DEFAULT_CONFIG_PATH
    defaults = AppConfig()
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        config = AppConfig(
            bind=data.get("bind", defaults.bind),
            port=int(data.get("port", defaults.port)),
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
        )
    else:
        config = defaults

    env_makes = _parse_env_list("FUJI_FRAME_CAMERA_MAKE")
    env_models = _parse_env_list("FUJI_FRAME_CAMERA_MODEL")
    if env_makes is not None:
        config.camera_make_allowlist = env_makes
    if env_models is not None:
        config.camera_model_allowlist = env_models

    return config


def _parse_env_list(key: str) -> Optional[List[str]]:
    raw = os.environ.get(key)
    if raw is None:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items
