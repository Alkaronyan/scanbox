# SCANBOX - Dynamic Video Switcher Module

A decoupled, deterministic video source switcher designed for embedded systems (Raspberry Pi CM4) running isolated within Docker containers. Facilitates hot-swapping between physical hardware cameras and emulated testing streams via a REST API and web UI, without breaking downstream video pipeline integrity.

## Project Architecture

### Containerization Philosophy (Strict)

Everything that *can* run inside a container *does*. The host OS is kept clean. Only the minimum that **must** live on the host is installed, fully automated by `setup_host.sh`.

**Mandatory host-only requirements:**
* **Docker Engine** — container runtime
* **Kernel headers** (`linux-headers-*`) — for out-of-tree v4l2loopback compilation inside Vid_Mux_TEST
* **git** — repository management

Everything else (GStreamer, Python, build toolchain, v4l2loopback sources, etc.) lives inside containers.

### Containers

* **Vid_Mux** — Production application. GStreamer input-selector pipeline, Flask REST API + Web UI, MJPEG HTTP stream. Ports: 5000 (API + UI + stream).
* **Vid_Mux_TEST** — Development scaffold. Compiles and loads v4l2loopback into the shared kernel, feeds synthetic SMPTE pattern to /dev/video200. Discarded in production.

### Device Mapping

| Internal path | Host source | Description |
|---|---|---|
| /dev/video100 | /dev/v4l/by-id/usb-046d_0809_5DD0F8C2-video-index0 | Physical USB camera (Logitech) |
| /dev/video200 | /dev/video200 (created by Vid_Mux_TEST) | Synthetic mock camera |

## Repository Structure

```
scanbox/
├── app/
│   ├── Vid_Mux/           # Production container
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   ├── main.py        # Process entry point (single process, two threads)
│   │   ├── switcher.py    # GStreamer pipeline + input-selector
│   │   ├── api.py         # Flask REST API + MJPEG stream + Web UI
│   │   ├── templates/
│   │   │   └── index.html
│   │   └── static/
│   │       └── style.css
│   └── snapshots/         # Bind-mounted snapshot storage (persists outside container)
├── test/
│   ├── Vid_Mux_TEST/      # Mock camera scaffold
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   └── mock_streamer.py
│   └── capture_test.sh    # Frame capture test tool
├── docs/
│   ├── ARCH_VID_MUX.md
│   ├── ARCH_VID_MUX_TEST_FRAMEWORK.md
│   └── RESTART_PROMPT.md  # LLM collaboration context
├── setup_host.sh          # Host provisioning (run once)
└── rebuild_vid_mux.sh     # Stop → rebuild → relaunch Vid_Mux
```

## Quick Start

### 1. Host provisioning (fresh Pi, run once)
```bash
sudo ./setup_host.sh
newgrp docker
```

### 2. Start the mock camera scaffold
```bash
cd test/Vid_Mux_TEST
docker build -t vid_mux_test .
KBUILD_DIR="$(dirname "$(readlink -f /lib/modules/$(uname -r)/build/scripts)")"
docker run -d --name vid_mux_test --privileged --network=host \
  -v /lib/modules:/lib/modules:ro \
  -v /usr/src:/usr/src:ro \
  -v "${KBUILD_DIR}:${KBUILD_DIR}:ro" \
  vid_mux_test
```

### 3. Build and run the production switcher
```bash
./rebuild_vid_mux.sh
```

### 4. Open the web UI
Navigate to `http://<pi-ip>:5000` in any browser on the local network.

## Web UI

* **Live stream** — MJPEG stream embedded directly in the browser (no plugins needed)
* **Source selector** — switch between cameras with click, Tab, or ← → keys
* **Snapshot** — capture and display the last frame (Space key)
* **Camera controls** — Pan/Tilt/Zoom/Focus panel (UI present, API pending)
* **Keyboard shortcuts** — F1 to open reference modal

## REST API (port 5000)

| Method | Path | Description |
|---|---|---|
| GET | / | Web UI |
| GET | /stream | Live MJPEG stream |
| GET | /api/v1/status | Active source info |
| POST | /api/v1/source | Switch source `{"source_id": 0\|1}` |
| POST | /api/v1/snapshot | Capture frame to disk |
| GET | /api/v1/snapshot/last | Retrieve last snapshot |