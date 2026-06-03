# Architectural Specification: Video Switcher Module (SCANBOX)

This document defines the technical architecture of the dynamic video source switching module for the SCANBOX project. The main goal is to allow hot-swapping between multiple V4L2 input sources, delivering a single unified output stream while isolating the business logic from both the user interface and the specific USB output hardware.

> **Containerization rule:** Everything that can be containerized is. The host
> only carries the mandatory runtime/kernel dependencies installed by
> `setup_host.sh` (Docker Engine, kernel headers, git). Any new host-mandatory
> requirement must be added to that script and documented here. See the README
> for the full philosophy.

## 1. Core Component: Container 1 (SCANBOX Application)

The application runs inside a Docker container with two concurrent threads: a GStreamer background thread and the Flask API main thread.

### A. Video Pipeline (per-source independent pipelines)

Each configured source runs in its own `Gst.Pipeline`. There is no `input-selector`. Switching means changing which pipeline's `appsink` writes to the shared `frame_queue`.

**Physical camera pipeline** (MJPEG passthrough — no decode/re-encode):
```
v4l2src device=/dev/videoN
  ! image/jpeg,width=640,height=480,framerate=30/1
  ! appsink name=output emit-signals=true max-buffers=2 drop=true sync=false
```

**Mock source pipelines** (two visually distinct patterns):
```
videotestsrc pattern=colors          # mock_0 → "MOCK 1: <timestamp>"
  ! video/x-raw,width=640,height=480,framerate=30/1
  ! timeoverlay text="MOCK 1: " ...
  ! videoconvert ! video/x-raw,format=I420
  ! jpegenc quality=85
  ! appsink ...

videotestsrc pattern=smpte           # mock_1 → "MOCK 2: <timestamp>"
  ! ...same chain...
```

**Frame routing:** `_make_callback(source_id)` is registered as the `new-sample` signal handler for each appsink. The callback checks `_active_source` under the global lock and only enqueues the frame if `source_id == _active_source`. All other pipelines run silently.

**Camera power management (lazy start):** pipelines are not started at boot. The first `POST /api/v1/heartbeat` call from the Web UI triggers `start_all()` in a background thread. If no heartbeat is received for `IDLE_TIMEOUT` seconds (default 30), the camera watchdog thread calls `stop_all()`, closing all V4L2 device handles. Cameras restart on the next heartbeat.

**Deterministic device mapping:** physical cameras are assigned to high-index slots (`/dev/video100`–`/dev/video103`) by `rebuild_vid_mux.sh` at launch time, using the immutable by-id paths under `/dev/v4l/by-id/`. Mock sources use `slot: null` in `SCANBOX_SOURCES` — no V4L2 device is ever opened for them.

**Output:** MJPEG HTTP stream served by Flask at `GET /stream`. The `frame_refresher` background thread continuously drains `frame_queue` into `_last_frame` (thread-safe). The MJPEG generator and snapshot endpoint both read from `_last_frame`.

### B. Signaling and Control Service (REST API)

Flask runs in the main thread. It accepts commands from the Web UI and external clients, modifies shared switcher state, and starts/stops individual GStreamer pipelines on demand.

*   **Protocol:** HTTP/JSON.
*   **Default Port:** `80`.

---

## 2. Interface Contract (API Endpoints)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Web UI |
| GET | `/stream` | Live MJPEG stream |
| GET | `/api/v1/status` | Active source, source list, running source ids |
| POST | `/api/v1/heartbeat` | Keep cameras alive; first call starts all pipelines |
| POST | `/api/v1/source` | Switch active source `{"source_id": N}` |
| POST | `/api/v1/source/<id>/start` | Start one source pipeline (debug) |
| POST | `/api/v1/source/<id>/stop` | Stop one source pipeline (debug) |
| POST | `/api/v1/snapshot` | Capture frame. Optional body: `{"filename": "name.jpg"}` |
| GET | `/api/v1/snapshot/last` | Retrieve last snapshot JPEG |
| GET | `/api/v1/snapshots` | List snapshots (paginated: `?offset=0&limit=5`) |
| GET | `/api/v1/snapshot/<filename>` | Retrieve a specific snapshot by filename |
| DELETE | `/api/v1/snapshot/<filename>` | Delete a specific snapshot |
| GET | `/api/v1/camera/controls` | V4L2 controls for active source (empty for mock) |
| POST | `/api/v1/camera/control` | Set V4L2 control `{"control": "saturation", "value": 128}` |

**`GET /api/v1/status` response:**
```json
{
  "status": "ok",
  "active_source": 0,
  "source_name": "UVC Camera (046d:0809)",
  "sources": [{"id": 0, "name": "...", "device": "..."}, ...],
  "running_sources": [0, 1]
}
```

**`POST /api/v1/heartbeat`:** updates `_last_heartbeat` timestamp. If cameras were stopped (idle), starts all pipelines in a background thread and returns `"cameras_starting": true`; otherwise `false`. Called every 10 s by the Web UI (`sendHeartbeat()` in `main.js`). The camera watchdog polls every 5 s; cameras stop after 30 s without a heartbeat.

---

## 3. User Interface (Web UI)

The web UI is a single-page application served by Flask at `GET /`. It connects to the MJPEG stream, calls the REST API for state changes, and renders V4L2 controls dynamically. No frontend framework — plain JS and CSS.

### Project-wide language rule

All user-visible text (UI labels, status messages, watermarks, button text, code comments, documentation) must be in English. No Spanish in any source file.

### JavaScript module structure

All JS lives in `static/js/`. Files are loaded in dependency order via `<script src>` tags; there are no ES modules or bundlers. Globals are declared in `main.js` and accessed by earlier files through the `window` scope.

**Load order is critical:** `ui.js` calls functions and reads globals defined in modules that load before and after it. Any top-level code in a module that reads a global from a later-loaded module will throw a `ReferenceError` and silently abort the rest of that module — including event listener registration.

**Globals declared in `main.js`:**

| Global | Type | Purpose |
|---|---|---|
| `sources` | Array | Source list from last `fetchStatus()`. |
| `activeId` | Number | Currently active source id. |
| `runningSourceIds` | Array | Source ids whose pipeline is currently running. |
| `zoomLevel` / `focusLevel` | Number | Fake PTZ bar state (placeholders). |
| `sliderTimers` | Object | Per-control debounce timers used by `controls.js`. |
| `debugMode` | Boolean | Whether debug mode is currently on. |
| `reInitializingSourceIds` | Set | Sources awaiting pipeline restart — drives the "REINITIALIZING…" watermark. |

| File | Responsibility |
|---|---|
| `api.js` | All `fetch()` wrappers. No DOM access. |
| `stream.js` | MJPEG `<img>` auto-reconnect watchdog. |
| `sources.js` | Source list rendering, `selectSource()`, `cycleSource()`, `startSource()`. |
| `controls.js` | V4L2 Camera Config widgets (slider / toggle / menu), reset. |
| `snapshot.js` | Snapshot POST + preview display. |
| `ui.js` | Section collapse, modal, fake zoom/focus bars, keyboard and Ctrl+Scroll wheel events. |
| `main.js` | Shared global declarations, `applyDebugMode()`, `updateStreamWatermark()`, `fetchStatus()` polling (every 3 s). |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Take snapshot |
| `Tab` / `←` `→` | Cycle / select video source |
| `Q` / `E` | Focus − / + (placeholder) |
| `Ctrl + Scroll` | Zoom in / out (placeholder) |
| `F1` | Open / close shortcuts modal |

### Debug mode

A pill toggle inside the F1 shortcuts modal enables debug mode. When active, `body.debug-mode` is set and elements with `.debug-only` become visible. Currently the only debug-only controls are the **Start / Stop** buttons for each source (used to manually restart or halt individual GStreamer source pipelines via `POST /api/v1/source/{id}/start` and `/stop`).

`applyDebugMode(on)` in `main.js` applies the body class and re-renders the source list so debug controls appear or disappear immediately.

### Stream watermark overlay

A `<div id="stream-watermark">` is absolutely positioned over the MJPEG `<img>` and updated by `updateStreamWatermark()` in `main.js` after every `fetchStatus()` call and after every `selectSource()`:

| State | Text | Colour |
|---|---|---|
| Active source is stopped, not reinitializing | `CAMERA STOPPED` | Red (`#f87171`) |
| Restart requested, waiting for confirmation | `REINITIALIZING…` | Amber (`#fbbf24`) |
| Active source is running | *(hidden)* | — |

The amber state is triggered from two paths, both before any API confirmation:
- **Page connect / reconnect after idle:** `sendHeartbeat()` in `main.js` receives `cameras_starting: true` and adds `activeId` to `reInitializingSourceIds`.
- **Manual restart button:** `startSource()` in `sources.js` adds the source id to `reInitializingSourceIds` directly before calling `apiStartSource()`.

### Status panel

`fetchStatus()` appends ` · Stopped` to the source name in `#status-source` when the active source is not in `runningSourceIds`. `selectSource()` applies the same logic immediately on switch.

### Camera Controls section

Starts **collapsed** by default (`.section-body.collapsed`). All PTZ placeholder buttons carry the `.dim` class (opacity 0.35, cursor not-allowed) because the PTZ API is not yet implemented.

### Camera name resolution

Physical camera display names are resolved at container startup by `_get_camera_card_name()` in `api.py`:

1. Read `/sys/class/video4linux/<dev>/name` (instant, no subprocess).
2. If absent, parse `v4l2-ctl -d <dev> --info` for the `Card type` line.
3. If both fail, fall back to the label-derived name (`_make_display_name(label)`).

Mock sources use `_make_display_name(label)`:
- `mock_0` → `"Mock Camera 1"`
- `mock_1` → `"Mock Camera 2"`

Detection is never attempted for mock sources.

**Known limitation:** Some cameras (e.g. Logitech `046d:0809`) do not expose a USB product string and report only a generic `UVC Camera (VID:PID)` card name. A planned enhancement is an optional `name` field in `SCANBOX_SOURCES` for manual overrides.

### Snapshot gallery

`snapshot.js` implements a paginated gallery over `GET /api/v1/snapshots`:
- Initial load: 5 most recent snapshots (newest first).
- "Load more" button fetches 15 additional entries per click; disappears when all snapshots are loaded.
- Selecting an entry displays it in the preview frame and enables the Download and Delete buttons.
- Delete calls `DELETE /api/v1/snapshot/<filename>`, then refreshes the list.
- Every new snapshot (Space key or Capture button) is prepended to the selector automatically.
