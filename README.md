# Fugi Frame

A lightweight local web server for showing camera-filtered photos from Apple Photos, balanced by shoot sessions, on any device with a browser.

## Disclaimer
Fugi Frame is an independent project and is not affiliated with, endorsed by, or sponsored by Fujifilm.

## How it works
- Reads the local Apple Photos library on macOS.
- Filters photos by EXIF camera make/model (default: Fujifilm).
- Groups photos into sessions using a configurable time gap (default: 10 minutes).
- Randomly selects a session, then a photo from that session, to avoid oversampling long shoots.
- Serves a fullscreen web UI with a smooth crossfade and preloading.

## Prerequisites
- macOS with Apple Photos synced.
- Python 3.10+.
- **Photos originals on disk**: In Photos > Settings > iCloud, choose **Download Originals to this Mac**.
- **Privacy permission**: Give your terminal Full Disk Access so `osxphotos` can read the Photos library.
  - System Settings → Privacy & Security → Full Disk Access → add Terminal (or your shell app).

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.json config.json
```

## Run
```bash
source .venv/bin/activate
python3 -m app.main --config config.json
```

Open the UI:
```
http://<mac-ip>:8765
```

Controls:
- Arrow left/right for previous/next
- Space for next

## Logging
On first start the app indexes your entire Photos library, which can take time with large libraries. While indexing, the server stays up and `/api/status` returns `indexing: true`.

To check status:
```
curl http://localhost:8765/api/status
```

## Configuration
Edit `config.json`:
- `session_gap_minutes`: time gap that starts a new shoot session.
- `selection_mode`: `shuffle` (default) or `random`.
- `change_interval_seconds`: time between photos.
- `transition_ms`: fade duration.
- `fit_mode`: `cover` (default) or `contain`.
- `max_image_width` / `max_image_height`: cached image size.

## Sources
- `source: "apple_photos"`: uses the local Apple Photos library via `osxphotos` (macOS only).
- `source: "immich"`: uses the Immich API and requires:
  - `immich_url`: base URL (for example `http://localhost:2283`, without trailing slash)
  - `immich_api_key`: API key sent via `x-api-key`
- Both sources apply `camera_make_allowlist` and `camera_model_allowlist`.
- Both sources can filter by effective image orientation after EXIF rotation:
  - `orientation_allowlist`: optional list containing `landscape`, `portrait`, and/or `square`.
  - Use `["landscape"]` for wide displays to avoid portrait photos being heavily center-cropped.
- Immich can also filter by people metadata:
  - `immich_person_allowlist`: optional Immich person IDs or exact person names.
  - `immich_person_match_mode`: `any` (default, at least one allowed person appears) or `all` (every allowed person appears).
  - Person IDs are safer than names because Immich person names can be empty until labeled.

### People filter
For Immich-only filtering, set:

```json
{
  "source": "immich",
  "immich_person_allowlist": ["person-id-sophie", "person-id-felix"],
  "immich_person_match_mode": "any"
}
```

Use `"any"` for photos containing Sophie or Felix or both; use `"all"` for photos containing both Sophie and Felix.
You can also override via env vars:

```
FUGI_FRAME_IMMICH_PERSON_ALLOWLIST=person-id-sophie,person-id-felix FUGI_FRAME_IMMICH_PERSON_MATCH_MODE=any python3 -m app.main --config config.json
```

### Orientation filter
For landscape-only displays, set:

```json
{
  "orientation_allowlist": ["landscape"]
}
```

The filter uses effective orientation after EXIF rotation, so camera files stored as raw landscape dimensions with rotation metadata can still be correctly classified as portrait.
You can also override via env vars:

```
FUGI_FRAME_ORIENTATION_ALLOWLIST=landscape python3 -m app.main --config config.json
```

### Camera filter
By default, the app only indexes photos whose camera make matches `FUJIFILM`.
To customize:

- Edit `config.json`:
  - `camera_make_allowlist`: e.g. `["CANON"]` or `["SONY", "NIKON"]`
  - `camera_model_allowlist`: optional narrower filter, e.g. `["X-T5"]`
- Or override via env vars (comma-separated):
  ```
  FUGI_FRAME_CAMERA_MAKE=CANON,SONY FUGI_FRAME_CAMERA_MODEL=R5 python3 -m app.main --config config.json
  ```

## Randomization logic
The goal is to avoid oversampling large shoots while still keeping the display fresh:

- **Session grouping**: Photos are sorted by capture time and grouped into sessions using `session_gap_minutes`. Each session represents a shoot.
- **Session selection**:  
  - `shuffle`: creates a randomized list of sessions and walks it once before reshuffling.  
  - `random`: chooses any session at random; if `avoid_consecutive_sessions` is true, it avoids picking the same session twice in a row.
- **Photo selection**: Once a session is chosen, a single photo is picked uniformly at random from that session.
- **History**: The server keeps a short in-memory history so the UI can go back/forward with arrow keys.

## Troubleshooting
- **No photos**: confirm your Fujifilm EXIF make is `FUJIFILM`. If not, add your specific model in `camera_model_allowlist`.
- **Missing originals**: ensure Photos is set to download originals to this Mac.
- **Permission errors**: grant Full Disk Access to the terminal running the server.

## Security
This server is intended for trusted local networks. If you expose it publicly, add network controls.
