# Architectural Specification: Video Switcher Module (SCANBOX)

This document defines the technical architecture of the dynamic video source switching module for the SCANBOX project. The main goal is to allow hot-swapping between multiple V4L2 input sources, delivering a single unified output stream while isolating the business logic from both the user interface and the specific USB output hardware.

> **Containerization rule:** Everything that can be containerized is. The host
> only carries the mandatory runtime/kernel dependencies installed by
> `setup_host.sh` (Docker Engine, kernel headers, git). Any new host-mandatory
> requirement must be added to that script and documented here. See the README
> for the full philosophy.

## 1. Core Component: Container 1 (SCANBOX Application)

The application runs isolated inside a Docker container and consists of two concurrent execution threads that communicate via thread-safe memory mechanisms.

### A. Video Pipeline (GStreamer Pipeline)
The processing engine uses GStreamer for low-level video capture, synchronization, and switching.

*   **Deterministic Input Mapping (Internal Sandboxing):**
    To isolate the application from dynamic kernel device assignments on the host OS, inputs inside this container are explicitly mapped to high-index static endpoints to prevent conflicts with legacy devices:
    *   `sink_0`: Bound to the internal static endpoint `/dev/video100`. At runtime, this is mapped externally to the physical camera's immutable hardware serial path located at `/dev/v4l/by-id/*`.
    *   `sink_1`: Mock/synthetic source. Uses `videotestsrc pattern=colors` with a `timeoverlay` directly inside the pipeline — does **not** read from `/dev/video200` via v4l2src. v4l2loopback rejects CAPTURE-side `S_FMT` when the OUTPUT side (mock_streamer's v4l2sink) already has the device open, making v4l2src on `/dev/video200` unusable while Vid_Mux_TEST is running.
*   **Central Element:** `input-selector`. This GStreamer core node maintains clock buffer synchronization for both sources. It allows alternating the active input instantly without breaking the output stream (preventing End-of-Stream or connectivity drops).
*   **Capture Element:** `valve` coupled with an image sink, used to extract frames on demand without stopping the pipeline.
*   **Output Node:** Agnostic encapsulation. In the development phase, it emits via network protocol (RTP/UDP); in the production phase, it will be reconfigured toward the UVC Gadget V4L2 sink (`v4l2sink`).

### B. Signaling and Control Service (REST API)
A lightweight microservice runs on a secondary thread to act as the external control interface for the container.

*   **Protocol:** HTTP/JSON.
*   **Default Port:** `80`.
*   **Mission:** Receive external commands, validate the payload, modify global state variables, and notify the GStreamer thread to change the active Pad on the `input-selector` element.

---

## 2. Interface Contract (API Endpoints)

### Source Switching
Changes the video source exposed by the switcher output.
*   **Path:** `POST /api/v1/source`
*   **Body (JSON):**
    ```json
    {
      "source_id": 0
    }
    ```
    *(Where `0` corresponds to the physical camera and `1` to the simulated camera).*
*   **Response (JSON):** `{"status": "success", "active_source": 0}`

### Frame Capture (Snapshot)
Extracts a frame from the active video stream and stores it on disk.
*   **Path:** `POST /api/v1/snapshot`
*   **Body (JSON):** `200 OK` (No initial mandatory parameters).
*   **Response (JSON):** `{"status": "success", "file_path": "/exports/snapshots/snap_20260531_142500.jpg"}`

---

## 3. User Interface (Web UI)

The web UI is a single-page application served by Flask at `GET /`. It connects to the MJPEG stream, calls the REST API for state changes, and renders V4L2 controls dynamically. No frontend framework — plain JS and CSS.

### JavaScript module structure

All JS lives in `static/js/`. Files are loaded in dependency order via `<script src>` tags; there are no ES modules or bundlers. Globals (`sources`, `activeId`, `zoomLevel`, `focusLevel`, `sliderTimers`) are declared in `main.js` and accessed by earlier files through the `window` scope.

**Load order is critical:** `ui.js` calls functions and reads globals defined in modules that load before and after it. Any top-level code in a module that reads a global from a later-loaded module will throw a `ReferenceError` and silently abort the rest of that module — including event listener registration.

| File | Responsibility |
|---|---|
| `api.js` | All `fetch()` wrappers. No DOM access. |
| `stream.js` | MJPEG `<img>` auto-reconnect watchdog. |
| `sources.js` | Source list rendering, `selectSource()`, `cycleSource()`. |
| `controls.js` | V4L2 Camera Config widgets (slider / toggle / menu), reset. |
| `snapshot.js` | Snapshot POST + preview display. |
| `ui.js` | Section collapse, modal, fake zoom/focus bars, keyboard and Ctrl+Scroll wheel events. |
| `main.js` | Shared global declarations, init calls, `fetchStatus()` polling (every 3 s). |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Take snapshot |
| `Tab` / `←` `→` | Cycle / select video source |
| `Q` / `E` | Focus − / + (placeholder) |
| `Ctrl + Scroll` | Zoom in / out (placeholder) |
| `F1` | Open / close shortcuts modal |

### Camera Controls section

Starts **collapsed** by default (`.section-body.collapsed`). All PTZ placeholder buttons carry the `.dim` class (opacity 0.35, cursor not-allowed) because the PTZ API is not yet implemented.

### Camera name resolution

Physical camera display names are resolved at container startup by `_get_camera_card_name()` in `api.py`:

1. Read `/sys/class/video4linux/<dev>/name` (instant, no subprocess).
2. If absent, parse `v4l2-ctl -d <dev> --info` for the `Card type` line.
3. If both fail, fall back to the label-derived name (`_make_display_name(label)`).

The mock source always uses the hardcoded name `"Mock Camera"` — detection is never attempted for it.

**Known limitation:** Some cameras (e.g. Logitech `046d:0809`) do not expose a USB product string and report only a generic `UVC Camera (VID:PID)` card name. A planned enhancement is an optional `name` field in `SCANBOX_SOURCES` for manual overrides.
