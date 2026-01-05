import os
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageOps

from .config import AppConfig

try:
    import osxphotos  # type: ignore
except Exception as exc:  # pragma: no cover - import-time guard
    osxphotos = None
    _OSXPHOTOS_IMPORT_ERROR = exc


@dataclass
class PhotoRecord:
    uuid: str
    date: datetime
    path: str
    camera_make: str
    camera_model: str


class LibraryIndex:
    def __init__(self, config: AppConfig):
        if config.selection_mode not in {"shuffle", "random"}:
            config.selection_mode = "shuffle"
        self._config = config
        self._lock = threading.Lock()
        self._photos_by_uuid: Dict[str, PhotoRecord] = {}
        self._sessions: List[List[str]] = []
        self._last_indexed: Optional[datetime] = None
        self._missing_paths: int = 0
        self._session_order: List[int] = []
        self._last_session: Optional[int] = None

        if config.random_seed is not None:
            random.seed(config.random_seed)

    @property
    def stats(self) -> Dict[str, object]:
        with self._lock:
            return {
                "photos": len(self._photos_by_uuid),
                "sessions": len(self._sessions),
                "last_indexed": self._last_indexed.isoformat() if self._last_indexed else None,
                "missing_paths": self._missing_paths,
            }

    def rebuild(self) -> None:
        if osxphotos is None:
            raise RuntimeError(
                "osxphotos is not available: {}".format(_OSXPHOTOS_IMPORT_ERROR)
            )

        photos_db = osxphotos.PhotosDB()
        photos = photos_db.photos()

        allow_makes = {m.upper() for m in self._config.camera_make_allowlist if m}
        allow_models = {m.upper() for m in self._config.camera_model_allowlist if m}

        records: List[PhotoRecord] = []
        missing_paths = 0
        for photo in photos:
            if not getattr(photo, "isphoto", True):
                continue
            if not photo.date:
                continue

            camera_make = (getattr(photo, "camera_make", "") or "").strip()
            camera_model = (getattr(photo, "camera_model", "") or "").strip()
            if not camera_make and hasattr(photo, "exif_info"):
                camera_make = (photo.exif_info or {}).get("Make", "")
            if not camera_model and hasattr(photo, "exif_info"):
                camera_model = (photo.exif_info or {}).get("Model", "")

            if allow_makes and not _matches_allowlist(camera_make, allow_makes):
                continue
            if allow_models and not _matches_allowlist(camera_model, allow_models):
                continue

            path = _resolve_photo_path(photo)
            if not path:
                missing_paths += 1
                continue

            records.append(
                PhotoRecord(
                    uuid=photo.uuid,
                    date=photo.date,
                    path=path,
                    camera_make=camera_make,
                    camera_model=camera_model,
                )
            )

        records.sort(key=lambda r: r.date)
        sessions = _build_sessions(records, self._config.session_gap_minutes)

        with self._lock:
            self._photos_by_uuid = {r.uuid: r for r in records}
            self._sessions = [[r.uuid for r in session] for session in sessions]
            self._last_indexed = datetime.now()
            self._missing_paths = missing_paths
            self._session_order = []
            self._last_session = None

    def pick_next(self) -> Optional[Tuple[int, PhotoRecord]]:
        with self._lock:
            if not self._sessions:
                return None

            session_index = self._pick_session_index()
            session = self._sessions[session_index]
            photo_uuid = random.choice(session)
            record = self._photos_by_uuid.get(photo_uuid)
            if record is None:
                return None
            return session_index, record

    def _pick_session_index(self) -> int:
        if self._config.selection_mode == "shuffle":
            if not self._session_order:
                self._session_order = list(range(len(self._sessions)))
                random.shuffle(self._session_order)
            session_index = self._session_order.pop(0)
        else:
            session_index = random.randrange(len(self._sessions))
            if (
                self._config.avoid_consecutive_sessions
                and self._last_session is not None
                and len(self._sessions) > 1
            ):
                while session_index == self._last_session:
                    session_index = random.randrange(len(self._sessions))

        self._last_session = session_index
        return session_index

    def ensure_cached(self, record: PhotoRecord, max_width: int, max_height: int, quality: int) -> str:
        cache_dir = self._config.cache_dir_expanded
        os.makedirs(cache_dir, exist_ok=True)

        cache_name = f"{record.uuid}_{max_width}x{max_height}.jpg"
        cache_path = os.path.join(cache_dir, cache_name)
        if os.path.exists(cache_path):
            return cache_path

        image = Image.open(record.path)
        image = ImageOps.exif_transpose(image)
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        image.save(cache_path, "JPEG", quality=quality, optimize=True)
        return cache_path

    def get_record(self, photo_id: str) -> Optional[PhotoRecord]:
        with self._lock:
            return self._photos_by_uuid.get(photo_id)



def _build_sessions(records: List[PhotoRecord], gap_minutes: int) -> List[List[PhotoRecord]]:
    if not records:
        return []

    gap_seconds = gap_minutes * 60
    sessions: List[List[PhotoRecord]] = []
    current: List[PhotoRecord] = [records[0]]
    last_date = records[0].date

    for record in records[1:]:
        if (record.date - last_date).total_seconds() > gap_seconds:
            sessions.append(current)
            current = []
        current.append(record)
        last_date = record.date

    if current:
        sessions.append(current)
    return sessions


def _matches_allowlist(value: str, allowlist: set) -> bool:
    if not value:
        return False
    upper = value.upper()
    return any(allowed in upper for allowed in allowlist)



def _resolve_photo_path(photo) -> Optional[str]:
    candidates = [
        getattr(photo, "path", None),
        getattr(photo, "path_edited", None),
        getattr(photo, "original_path", None),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if isinstance(candidate, list):
            for path in candidate:
                if path and os.path.exists(path):
                    return path
        else:
            if os.path.exists(candidate):
                return candidate
    return None


class IndexRefresher(threading.Thread):
    def __init__(self, index: LibraryIndex, refresh_minutes: int):
        super().__init__(daemon=True)
        self._index = index
        self._refresh_seconds = max(refresh_minutes, 1) * 60
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._refresh_seconds):
            try:
                self._index.rebuild()
            except Exception:
                # Keep the server alive even if refresh fails.
                continue

    def stop(self) -> None:
        self._stop_event.set()
