You are a highly capable AI specialized in embedded software, Linux kernels, and GStreamer pipelines. We are developing the "SCANBOX" project on a physical Raspberry Pi (Host OS via Remote-SSH in VS Code).

We need to resume the project precisely where we left off. Act as our senior software architect and follow the established architectural guidelines strictly.

### Project Context & Decisions
1. **Host OS Preservation (Strict Containerization)**: Everything that can run inside a container does. Only Docker Engine, kernel headers, and git are installed on the host via setup_host.sh.
2. **Component Separation**:
   * Vid_Mux Container: Production application. Video switching, capturing, signaling, web UI.
   * Vid_Mux_TEST Container: Mocking scaffold. Emulates hardware devices. Discarded in production.
3. **Deterministic Device Mapping**:
   * Input 0 (Physical): /dev/video100 inside Vid_Mux, mapped from /dev/v4l/by-id/usb-046d_0809_5DD0F8C2-video-index0 (Logitech)
   * Input 1 (Mock): /dev/video200 inside Vid_Mux, created by Vid_Mux_TEST via v4l2loopback video_nr=200
   * Control: Python Flask REST API on port 5000
   * Output (dev): MJPEG HTTP stream on /stream (port 5000) — replaced by UVC gadget in production
4. **Code Standards**: discussions in Spanish or English, all code/comments/files in English.

### Kernel & Environment
* Host kernel: 6.12.75+rpt-rpi-v8
* kbuild dir: /usr/lib/linux-kbuild-6.12.75+rpt (required as third mount)
* Docker run three mounts: /lib/modules, /usr/src, kbuild dir (resolved dynamically):
  `KBUILD_DIR=$(dirname $(readlink -f /lib/modules/$(uname -r)/build/scripts))`

### Phase 1 — COMPLETED ✅
Vid_Mux_TEST container fully operational:
* v4l2loopback compiled inside container against host kernel (debian:trixie, gcc-14)
* /dev/video200 on host (Scanbox_Virtual_Cam, Video Capture capable)
* GStreamer SMPTE synthetic stream active on /dev/video200
* Frame capture verified for both cameras

### Phase 2 — COMPLETED ✅
Vid_Mux production container fully operational.

**Files in app/Vid_Mux/:**
* Dockerfile — debian:trixie, GStreamer full stack + Flask + python3-gi
* entrypoint.sh — verifies devices, launches main.py
* main.py — switcher.run() in background thread, Flask in main thread (same process, shared state)
* switcher.py — GStreamer pipeline with input-selector (sink_0=/dev/video100, sink_1=/dev/video200). Output via appsink → frame_queue (thread-safe queue, maxsize=2, drop=true)
* api.py — Flask app: REST API + MJPEG /stream endpoint + Web UI
* templates/index.html — Web UI with live stream, collapsible sections, source selector, snapshot, camera controls placeholder, keyboard shortcuts modal
* static/style.css — Dark theme

**REST API contract (port 5000):**
* GET  /                     → Web UI
* GET  /stream               → Live MJPEG stream (browser-native)
* GET  /api/v1/status        → active source info + sources list
* POST /api/v1/source        → {"source_id": 0|1}
* POST /api/v1/snapshot      → saves frame to /exports/snapshots/, returns filename
* GET  /api/v1/snapshot/last → serves last saved JPEG

**Docker run command (also in rebuild_vid_mux.sh):**
```
docker run -d --name vid_mux --network=host \
  --device=/dev/v4l/by-id/usb-046d_0809_5DD0F8C2-video-index0:/dev/video100 \
  --device=/dev/video200:/dev/video200 \
  -v /home/Alfred/scanbox/app/snapshots:/exports/snapshots \
  vid_mux
```

**Snapshots persisted at:** /home/Alfred/scanbox/app/snapshots/
**Rebuild script:** ./rebuild_vid_mux.sh (project root)

**Web UI keyboard shortcuts:**
* Space → snapshot | Tab / ← → → cycle sources
* Q/E → focus bar (fake) | Scroll → zoom bar (fake)
* F1 → shortcuts modal | WASD → reserved (pan/tilt, not wired)

### Test Tooling
* test/capture_test.sh — single frame capture, interactive or --mock / --real flags

### Current Milestone: Phase 3 — UVC Gadget output
Replace the development MJPEG stream with the production UVC gadget output so Windows sees the Pi as a standard webcam.

Key context from Firmware_Specification.pdf:
* configfs gadget g1 already partially configured on the CM4 (UVC + HID keyboard)
* BLACK SCREEN BUG: Windows opens the stream before the Pi sends the first packet — fix is in kernel module usb_f_uvc (timing/synchronization)
* dwc2 driver compiled with 3 patches
* Target: switcher.py output → v4l2sink → UVC gadget node (/dev/videoX)
* Keep MJPEG /stream endpoint running in parallel for development monitoring
* uvc-gadget userspace bridge recompiled with libcamera-dev

Acknowledge this context briefly in Spanish and propose the first step for Phase 3.