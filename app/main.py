import argparse
import logging
import os
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, load_config
from .indexer import IndexRefresher, LibraryIndex



def _build_app(config: AppConfig) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("fuji_frame")
    app = FastAPI(title="Fugi Frame")

    index = LibraryIndex(config)
    app.state.index_error = None
    app.state.indexing = False

    def run_rebuild():
        app.state.indexing = True
        try:
            index.rebuild()
            app.state.index_error = None
        except Exception as exc:
            app.state.index_error = str(exc)
            logger.exception("Indexing failed: %s", exc)
        finally:
            app.state.indexing = False

    threading.Thread(target=run_rebuild, daemon=True).start()

    app.state.index = index

    refresher = IndexRefresher(
        index,
        config.index_refresh_minutes,
        on_start=lambda: setattr(app.state, "indexing", True),
        on_done=lambda: setattr(app.state, "indexing", False),
    )
    refresher.start()
    app.state.refresher = refresher

    @app.get("/api/status")
    def status():
        return {
            "ok": True,
            "index_error": app.state.index_error,
            "indexing": app.state.indexing,
            "stats": index.stats,
        }

    @app.get("/api/config")
    def config_endpoint():
        return {
            "change_interval_seconds": config.change_interval_seconds,
            "transition_ms": config.transition_ms,
            "fit_mode": config.fit_mode,
        }

    @app.post("/api/reindex")
    def reindex():
        threading.Thread(target=run_rebuild, daemon=True).start()
        return {"ok": True, "indexing": True}

    @app.get("/api/next")
    def next_photo():
        if app.state.index_error:
            raise HTTPException(status_code=500, detail=app.state.index_error)
        if app.state.indexing:
            raise HTTPException(status_code=503, detail="Indexing in progress")
        result = index.next_with_history()
        if result is None:
            raise HTTPException(status_code=404, detail="No eligible photos found")

        session_index, record = result
        return {
            "id": record.uuid,
            "session": session_index,
            "date": record.date.isoformat(),
            "camera_make": record.camera_make,
            "camera_model": record.camera_model,
            "url": f"/api/image/{record.uuid}",
        }

    @app.get("/api/prev")
    def prev_photo():
        if app.state.index_error:
            raise HTTPException(status_code=500, detail=app.state.index_error)
        if app.state.indexing:
            raise HTTPException(status_code=503, detail="Indexing in progress")
        result = index.prev_with_history()
        if result is None:
            raise HTTPException(status_code=404, detail="No previous photo")

        session_index, record = result
        return {
            "id": record.uuid,
            "session": session_index,
            "date": record.date.isoformat(),
            "camera_make": record.camera_make,
            "camera_model": record.camera_model,
            "url": f"/api/image/{record.uuid}",
        }

    @app.get("/api/image/{photo_id}")
    def image(photo_id: str, w: Optional[int] = None, h: Optional[int] = None):
        if app.state.index_error:
            raise HTTPException(status_code=500, detail=app.state.index_error)
        if app.state.indexing:
            raise HTTPException(status_code=503, detail="Indexing in progress")

        record = index.get_record(photo_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Photo not found")

        width = w or config.max_image_width
        height = h or config.max_image_height
        try:
            path = index.ensure_cached(record, width, height, config.jpeg_quality)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return FileResponse(path, media_type="image/jpeg")

    web_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))
    app.mount("/", StaticFiles(directory=web_root, html=True), name="web")

    return app



def main():
    parser = argparse.ArgumentParser(description="Fugi Frame server")
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="Path to config.json (default: ./config.json)",
    )
    args = parser.parse_args()

    config = load_config(args.config_path)
    app = _build_app(config)

    import uvicorn

    uvicorn.run(app, host=config.bind, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
