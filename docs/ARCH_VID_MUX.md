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
    *   `sink_1`: Bound to the internal static endpoint `/dev/video200`. At runtime, this is linked to the fixed loopback node created by the testing scaffold.
*   **Central Element:** `input-selector`. This GStreamer core node maintains clock buffer synchronization for both sources. It allows alternating the active input instantly without breaking the output stream (preventing End-of-Stream or connectivity drops).
*   **Capture Element:** `valve` coupled with an image sink, used to extract frames on demand without stopping the pipeline.
*   **Output Node:** Agnostic encapsulation. In the development phase, it emits via network protocol (RTP/UDP); in the production phase, it will be reconfigured toward the UVC Gadget V4L2 sink (`v4l2sink`).

### B. Signaling and Control Service (REST API)
A lightweight microservice runs on a secondary thread to act as the external control interface for the container.

*   **Protocol:** HTTP/JSON.
*   **Default Port:** `5000`.
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

## 3. User Interface (UI) Decoupling

To satisfy the physical control requirements (Raspberry Pi keyboard, direction arrows, and shortcuts), the architecture delegates peripheral event capturing outside the main application:

1.  A daemon script on the host (or a peripheral micro-container) listens to binary events from the physical Linux keyboard (`/dev/input/eventX`).
2.  Upon detecting a `Right / Left Arrow` event or the capture shortcut, this daemon translates the physical keystroke into an HTTP request (`curl` / `requests`) targeting Container 1's API.
3.  **Result:** The Switcher remains completely agnostic of how the command was generated, ensuring full interchangeability of the control hardware.
