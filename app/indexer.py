import logging
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
        self._history: List[Tuple[int, str]] = []
        self._history_index: int = -1
        self._history_limit: int = 500
        self._scan_total: int = 0
        self._scan_count: int = 0
        self._matched_count: int = 0

        if config.random_seed is not None:
            random.seed(config.random_seed)

        self._logger = logging.getLogger("fuji_frame")

    @property
    def stats(self) -> Dict[str, object]:
        with self._lock:
            return {
                "photos": len(self._photos_by_uuid),
                "sessions": len(self._sessions),
                "last_indexed": self._last_indexed.isoformat() if self._last_indexed else None,
                "missing_paths": self._missing_paths,
                "scanned": self._scan_count,
                "total": self._scan_total,
                "matched": self._matched_count,
            }

    def rebuild(self) -> None:
        if osxphotos is None:
            raise RuntimeError(
                "osxphotos is not available: {}".format(_OSXPHOTOS_IMPORT_ERROR)
            )

        start = time.time()
        photos_db = osxphotos.PhotosDB()
        photos = photos_db.photos()
        total = len(photos)
        self._logger.info(
            "Indexing started. Library=%s TotalPhotos=%d",
            getattr(photos_db, "library_path", "unknown"),
            total,
        )

        with self._lock:
            self._scan_total = total
            self._scan_count = 0
            self._matched_count = 0

        allow_makes = {m.upper() for m in self._config.camera_make_allowlist if m}
        allow_models = {m.upper() for m in self._config.camera_model_allowlist if m}

        records: List[PhotoRecord] = []
        missing_paths = 0
        matched_count = 0
        for idx, photo in enumerate(photos, start=1):
            if not getattr(photo, "isphoto", True):
                continue
            if not photo.date:
                continue

            camera_make = (getattr(photo, "camera_make", "") or "").strip()
            camera_model = (getattr(photo, "camera_model", "") or "").strip()
            if hasattr(photo, "exif_info") and photo.exif_info:
                exif = photo.exif_info
                if not camera_make:
                    if isinstance(exif, dict):
                        camera_make = exif.get("Make") or exif.get("camera_make") or ""
                    else:
                        camera_make = getattr(exif, "camera_make", "") or getattr(exif, "make", "")
                if not camera_model:
                    if isinstance(exif, dict):
                        camera_model = exif.get("Model") or exif.get("camera_model") or ""
                    else:
                        camera_model = getattr(exif, "camera_model", "") or getattr(exif, "model", "")

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
            matched_count += 1
            if idx % 2000 == 0:
                self._logger.info("Scanned %d/%d photos...", idx, total)
            if idx % 500 == 0:
                with self._lock:
                    self._scan_count = idx
                    self._matched_count = matched_count

        records.sort(key=lambda r: r.date)
        sessions = _build_sessions(records, self._config.session_gap_minutes)

        with self._lock:
            self._photos_by_uuid = {r.uuid: r for r in records}
            self._sessions = [[r.uuid for r in session] for session in sessions]
            self._last_indexed = datetime.now()
            self._missing_paths = missing_paths
            self._session_order = []
            self._last_session = None
            self._history = []
            self._history_index = -1
            self._scan_count = total
            self._matched_count = matched_count

        self._logger.info(
            "Indexing complete. Matched=%d Sessions=%d MissingPaths=%d Duration=%.1fs",
            len(records),
            len(sessions),
            missing_paths,
            time.time() - start,
        )

    def pick_next(self) -> Optional[Tuple[int, PhotoRecord]]:
        with self._lock:
            return self._pick_next_record()

    def next_with_history(self) -> Optional[Tuple[int, PhotoRecord]]:
        with self._lock:
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                session_index, photo_uuid = self._history[self._history_index]
                record = self._photos_by_uuid.get(photo_uuid)
                if record:
                    return session_index, record

            result = self._pick_next_record()
            if result is None:
                return None
            session_index, record = result
            self._history.append((session_index, record.uuid))
            if len(self._history) > self._history_limit:
                overflow = len(self._history) - self._history_limit
                self._history = self._history[overflow:]
                self._history_index = max(-1, self._history_index - overflow)
            self._history_index = len(self._history) - 1
            return result

    def prev_with_history(self) -> Optional[Tuple[int, PhotoRecord]]:
        with self._lock:
            if self._history_index <= 0:
                return None
            self._history_index -= 1
            session_index, photo_uuid = self._history[self._history_index]
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

    def _pick_next_record(self) -> Optional[Tuple[int, PhotoRecord]]:
        if not self._sessions:
            return None
        session_index = self._pick_session_index()
        session = self._sessions[session_index]
        photo_uuid = random.choice(session)
        record = self._photos_by_uuid.get(photo_uuid)
        if record is None:
            return None
        return session_index, record

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
    def __init__(
        self,
        index: LibraryIndex,
        refresh_minutes: int,
        on_start=None,
        on_done=None,
    ):
        super().__init__(daemon=True)
        self._index = index
        self._refresh_seconds = max(refresh_minutes, 1) * 60
        self._stop_event = threading.Event()
        self._on_start = on_start
        self._on_done = on_done

    def run(self) -> None:
        while not self._stop_event.wait(self._refresh_seconds):
            try:
                if self._on_start:
                    self._on_start()
                self._index.rebuild()
                if self._on_done:
                    self._on_done()
            except Exception:
                # Keep the server alive even if refresh fails.
                continue

    def stop(self) -> None:
        self._stop_event.set()
