import logging
import os
import random
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import httpx
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
    source_url: Optional[str] = None


class ApplePhotosSource:
    def __init__(
        self,
        config: AppConfig,
        logger: logging.Logger,
        on_progress: Optional[Callable[[int, int, int], None]] = None,
    ):
        self._config = config
        self._logger = logger
        self._on_progress = on_progress
        self.total_assets: int = 0
        self.scanned_assets: int = 0
        self.matched_assets: int = 0
        self.missing_paths: int = 0

    def fetch_records(self) -> List[PhotoRecord]:
        if osxphotos is None:
            raise RuntimeError(
                "osxphotos is not available: {}".format(_OSXPHOTOS_IMPORT_ERROR)
            )

        photos_db = osxphotos.PhotosDB()
        photos = photos_db.photos()
        total = len(photos)
        self.total_assets = total
        self._logger.info(
            "Indexing started. Library=%s TotalPhotos=%d",
            getattr(photos_db, "library_path", "unknown"),
            total,
        )

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
            if idx % 500 == 0 and self._on_progress:
                self._on_progress(total, idx, matched_count)

        self.scanned_assets = total
        self.matched_assets = matched_count
        self.missing_paths = missing_paths
        if self._on_progress:
            self._on_progress(total, total, matched_count)
        return records


class ImmichSource:
    def __init__(
        self,
        config: AppConfig,
        logger: logging.Logger,
        on_progress: Optional[Callable[[int, int, int], None]] = None,
    ):
        self._config = config
        self._logger = logger
        self._on_progress = on_progress
        self.total_assets: int = 0
        self.scanned_assets: int = 0
        self.matched_assets: int = 0
        self.missing_paths: int = 0
        self._base_url = (config.immich_url or "").rstrip("/")

    def fetch_records(self) -> List[PhotoRecord]:
        if not self._base_url:
            raise RuntimeError("immich_url is required when source=immich")
        if not self._config.immich_api_key:
            raise RuntimeError("immich_api_key is required when source=immich")

        allow_makes = {m.upper() for m in self._config.camera_make_allowlist if m}
        allow_models = {m.upper() for m in self._config.camera_model_allowlist if m}

        headers = {
            "x-api-key": self._config.immich_api_key,
            "Content-Type": "application/json",
        }
        page = 1
        size = 1000
        fetched_count = 0
        matched_count = 0
        records: List[PhotoRecord] = []

        # Build server-side make filter — pass each allowed make to Immich
        # (Immich search/metadata supports a single "make" filter per request;
        # we issue one request per make and merge results)
        make_queries: List[Optional[str]] = list(allow_makes) if allow_makes else [None]

        self._logger.info("Immich indexing started. URL=%s", self._base_url)
        with httpx.Client(timeout=60.0) as client:
            for make_filter in make_queries:
                page = 1
                while True:
                    body: dict = {"page": page, "size": size, "type": "IMAGE", "withExif": True}
                    if make_filter:
                        body["make"] = make_filter

                    response = client.post(
                        f"{self._base_url}/api/search/metadata",
                        headers=headers,
                        json=body,
                    )
                    response.raise_for_status()
                    data = response.json()
                    assets = data.get("assets", {}).get("items", [])
                    if not assets:
                        break

                    for asset in assets:
                        if not isinstance(asset, dict):
                            continue
                        fetched_count += 1
                        if fetched_count % 1000 == 0:
                            self._logger.info("Fetched %d assets from Immich...", fetched_count)
                        if self._on_progress and fetched_count % 500 == 0:
                            self._on_progress(fetched_count, fetched_count, matched_count)

                        asset_id = str(asset.get("id") or "").strip()
                        if not asset_id:
                            continue

                        date_value = asset.get("fileCreatedAt")
                        date = _parse_iso_datetime(date_value)
                        if date is None:
                            continue

                        # Parse make/model from originalPath: .../admin/{make}/{model}/lens/file.jpg
                        camera_make, camera_model = _parse_make_model_from_path(
                            asset.get("originalPath") or ""
                        )

                        if allow_models and not _matches_allowlist(camera_model, allow_models):
                            continue

                        records.append(
                            PhotoRecord(
                                uuid=asset_id,
                                date=date,
                                path="",
                                camera_make=camera_make,
                                camera_model=camera_model,
                                source_url=f"{self._base_url}/api/assets/{asset_id}/original",
                            )
                        )
                        matched_count += 1

                    if len(assets) < size:
                        break
                    page += 1

        self.total_assets = fetched_count
        self.scanned_assets = fetched_count
        self.matched_assets = matched_count
        if self._on_progress:
            self._on_progress(fetched_count, fetched_count, matched_count)
        return records


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
        start = time.time()
        with self._lock:
            self._scan_total = 0
            self._scan_count = 0
            self._matched_count = 0

        if self._config.source == "immich":
            source = ImmichSource(self._config, self._logger, self._set_scan_progress)
        else:
            source = ApplePhotosSource(self._config, self._logger, self._set_scan_progress)

        records = source.fetch_records()

        records.sort(key=lambda r: r.date)
        sessions = _build_sessions(records, self._config.session_gap_minutes)

        missing_paths = source.missing_paths
        total = source.total_assets
        scanned = source.scanned_assets
        matched_count = source.matched_assets
        with self._lock:
            self._photos_by_uuid = {r.uuid: r for r in records}
            self._sessions = [[r.uuid for r in session] for session in sessions]
            self._last_indexed = datetime.now()
            self._missing_paths = missing_paths
            self._session_order = []
            self._last_session = None
            self._history = []
            self._history_index = -1
            self._scan_total = total
            self._scan_count = scanned
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

    def _set_scan_progress(self, total: int, scanned: int, matched: int) -> None:
        with self._lock:
            self._scan_total = total
            self._scan_count = scanned
            self._matched_count = matched

    def ensure_cached(self, record: PhotoRecord, max_width: int, max_height: int, quality: int) -> str:
        cache_dir = self._config.cache_dir_expanded
        os.makedirs(cache_dir, exist_ok=True)

        cache_name = f"{record.uuid}_{max_width}x{max_height}.jpg"
        cache_path = os.path.join(cache_dir, cache_name)
        if os.path.exists(cache_path):
            return cache_path

        source_path = record.path
        downloaded_tmp_path: Optional[str] = None
        tmp_cache_path = f"{cache_path}.tmp"
        if record.source_url:
            if not self._config.immich_api_key:
                raise RuntimeError("immich_api_key is required for remote sources")
            with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".img", delete=False) as tmp_file:
                downloaded_tmp_path = tmp_file.name
            with httpx.Client(timeout=120.0) as client:
                with client.stream(
                    "GET",
                    record.source_url,
                    headers={"x-api-key": self._config.immich_api_key},
                ) as response:
                    response.raise_for_status()
                    with open(downloaded_tmp_path, "wb") as out_file:
                        for chunk in response.iter_bytes():
                            if chunk:
                                out_file.write(chunk)
            source_path = downloaded_tmp_path

        if not source_path:
            raise RuntimeError("Photo source is missing")

        try:
            with Image.open(source_path) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                image.save(tmp_cache_path, "JPEG", quality=quality, optimize=True)
            os.replace(tmp_cache_path, cache_path)
        finally:
            if os.path.exists(tmp_cache_path):
                os.remove(tmp_cache_path)
            if downloaded_tmp_path and os.path.exists(downloaded_tmp_path):
                os.remove(downloaded_tmp_path)
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


def _parse_make_model_from_path(path: str) -> tuple:
    """Extract camera make and model from Immich originalPath.

    Immich stores files as: /data/library/{user}/{make}/{model}/{lens}/{filename}
    Returns (make, model) strings, empty strings if not parseable.
    """
    parts = path.replace("\\", "/").split("/")
    # Find the user segment — everything after 'library/' or 'upload/'
    for marker in ("library", "upload"):
        try:
            idx = parts.index(marker)
            # parts[idx+1] = user, parts[idx+2] = make, parts[idx+3] = model
            if len(parts) > idx + 3:
                return parts[idx + 2], parts[idx + 3]
        except ValueError:
            continue
    return "", ""


def _parse_iso_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    parsed = value.strip()
    if not parsed:
        return None
    if parsed.endswith("Z"):
        parsed = f"{parsed[:-1]}+00:00"
    try:
        return datetime.fromisoformat(parsed)
    except ValueError:
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
