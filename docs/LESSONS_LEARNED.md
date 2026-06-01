# LESSONS_LEARNED.md — SCANBOX Project

## What this file is and how to use it

This file captures non-obvious technical discoveries, dead ends, and hard-won
knowledge accumulated during the development of SCANBOX. It exists because the
RESTART_PROMPT only captures *what works* — this file captures *why certain
things don't work* and *what to do instead*.

**For the LLM agent:** Every time you are asked to update this file, follow
this protocol exactly:

1. Read the entire file first.
2. Add a new dated entry at the bottom of the relevant section (or create a
   new section if none fits).
3. Be concise — one paragraph or a short bullet list per lesson.
4. Never delete existing entries. If something is no longer true, add a note
   below the original entry explaining when and why it changed.
5. After updating, remind the human to commit the file.

---

## Section 1 — GStreamer Pipeline

**`io-mode=rw` incompatible with v4l2loopback 0.15.3**
Using `io-mode=rw` on a `v4l2src` reading from `/dev/video200` (v4l2loopback)
causes a `not-negotiated` error and no frames are produced. The correct mode
is the default (MMAP / Streaming). Remove `io-mode=rw` entirely from the mock
camera source segment.

> **2026-06-01 — superseded.** Reading `/dev/video200` via `v4l2src` is not
> possible at all while `mock_streamer.py` (v4l2sink) has the device open —
> see the S_FMT entry below. The mock source now uses `videotestsrc` inside
> the pipeline and never touches v4l2loopback from the CAPTURE side.

**`input-selector` blocks on switch without `sync-streams=false`**
When switching to a pad that has not yet received a buffer, the `input-selector`
blocks indefinitely, causing the output stream to freeze on the last frame.
Always set `sync-streams=false` on the `input-selector` element.

**`leaky=downstream` required on inactive source queues**
Without `leaky=downstream max-size-buffers=2` on each source queue, buffers
accumulate while a source is inactive and cause a latency spike on switch.

**Physical cameras output MJPEG natively — do not re-encode**
The Logitech C270 and C920 output `image/jpeg` natively. The pipeline should
use `jpegdec ! videoconvert ! video/x-raw,format=I420` to decode, not
re-encode from raw. Attempting to negotiate `video/x-raw` directly from these
cameras fails.

**Mock camera (v4l2loopback) outputs `video/x-raw,format=YUY2`**
The `mock_streamer.py` writes YUY2 frames. The `vid_mux` pipeline must
negotiate `video/x-raw` (not `image/jpeg`) for the mock source and convert
to I420 for the `input-selector`.

> **2026-06-01 — superseded.** `mock_streamer.py` now outputs I420, but more
> importantly `vid_mux` no longer reads from `/dev/video200` at all — the mock
> source is `videotestsrc` inside the pipeline. See S_FMT entry below.

**v4l2loopback rejects CAPTURE-side `S_FMT` while OUTPUT side is open** *(2026-06-01)*
`v4l2loopback` refuses a `S_FMT` ioctl from any CAPTURE-side reader (e.g. `v4l2src`)
while the OUTPUT side (`v4l2sink` in `mock_streamer.py`) already has the device open.
GStreamer's `v4l2src` always issues `S_FMT` during caps negotiation — this cannot
be suppressed. The failure is silent: the pipeline enters PLAYING state but produces
no frames; the MJPEG stream serves the last cached frame indefinitely.

Confirmed with:
```bash
gst-launch-1.0 v4l2src device=/dev/video200 io-mode=rw num-buffers=3 ! fakesink
# → Call to S_FMT failed for YU12 @ 640x480: Device or resource busy
```

**Fix:** replace `v4l2src device=/dev/video200` with `videotestsrc pattern=colors`
+ `timeoverlay` directly inside the vid_mux GStreamer pipeline. The mock source
never reads from v4l2loopback. `vid_mux_test` still runs and keeps `/dev/video200`
alive as a boot-readiness signal, but vid_mux does not need the device mapped.

**GStreamer appsink backpressure freezes `_last_frame` after stream consumer disconnects** *(2026-06-01)*
When `emit-signals=true, max-buffers=2, drop=true` is set on the appsink, `_on_new_sample`
stops being called as soon as the buffer fills up and no downstream consumer is draining it.
The MJPEG generator (`_mjpeg_generator`) was the only consumer of `frame_queue`. Once a
browser tab closed, the generator exited, the queue filled, and the appsink stalled.
`_last_frame` was frozen at whatever frame was last written — all subsequent snapshot
requests returned the same identical JPEG, confirmed by identical MD5 hashes across 15
consecutive snapshots.

Fix: add a permanent background daemon thread (`frame_refresher`) in `api.py` that
continuously drains `frame_queue` regardless of whether any stream client is connected.
The MJPEG generator now reads from `_last_frame` (protected by a lock) rather than
competing with the refresher for queue items. Started in `main.py` after the GStreamer
thread.

**`docker compose restart` does not use a newly built image** *(2026-06-01)*
`docker compose restart vid_mux` reuses the existing container, which was created from
the old image. The new image is never consulted. To pick up a rebuilt image:
```bash
docker stop vid_mux && docker rm vid_mux && docker compose up -d --no-deps vid_mux
# or force compose to rebuild and recreate:
docker compose up -d --no-deps --build vid_mux
```
Symptom: code changes appear to have no effect after a restart; log lines from new code
never appear.

**ffmpeg `blend` filter requires identical input dimensions** *(2026-06-01)*
The `blend=all_mode=difference` filter fails silently and produces no `YAVG` output when
the two input videos have different resolutions (e.g. physical camera at 1920×1080 vs
mock source at 640×480). `frames_are_different()` always returned False, making all
switching tests pass incorrectly.

Fix: normalize both inputs to a common resolution before blending:
```
[0:v]scale=640:480[a];[1:v]scale=640:480[b];[a][b]blend=all_mode=difference,signalstats
```

**Snapshot filename collision at 1-second resolution** *(2026-06-01)*
The default snapshot filename format is `snap_YYYY_MM_DD__HH_MM_SS.jpg` (one-second
precision). Two snapshots taken within the same second overwrite each other, producing
a zero-difference result in frame comparison tests. Fixed by: (a) using the named
filename API parameter for test snapshots (`{"filename": "descriptive_name.jpg"}`), and
(b) keeping a `time.sleep(1.1)` between paired snapshots as a belt-and-suspenders guard.

---

## Section 2 — Docker & Container Management

**`docker cp` + `docker restart` vs full rebuild**
For changes to Python files or templates (`.py`, `.html`, `.css`), a full
`docker build` is wasteful and takes minutes. Use instead:
```bash
docker cp Vid_Mux/switcher.py vid_mux:/opt/vid_mux/switcher.py
docker restart vid_mux
```
A full rebuild is only needed when the Dockerfile or installed packages change.

**Flask templates are baked into the Docker image — editing the host file has no effect** *(2026-06-01)*
Flask's `render_template()` reads from the template directory that was `COPY`'d
into the image at build time. Editing `Vid_Mux/templates/index.html` on the host
does nothing to a running container. To update a template without a full rebuild:
```bash
docker cp Vid_Mux/templates/index.html vid_mux:/opt/vid_mux/templates/index.html
docker restart vid_mux
```
With `debug=False` (production mode), Jinja2 also caches templates in memory on
first render — a restart is required even after `docker cp`.

**Docker build cache is aggressive — changes may not be picked up**
If a rebuild completes suspiciously fast and the behavior hasn't changed, the
cache is serving stale layers. Force a fresh build with:
```bash
docker build --no-cache -t vid_mux Vid_Mux/
```

**`docker compose up -d vid_mux` recreates all services, not just one**
Due to dependency declarations in `docker-compose.yml`, compose may attempt
to recreate `vid_mux_test` even when only `vid_mux` is requested. Use the
rebuild script `rebuild_vid_mux.sh` instead, which manages each
container independently.

**`/tmp/scanbox_cameras.env` ownership conflict**
The systemd service runs as root and creates `/tmp/scanbox_cameras.env` owned
by root. If `rebuild_vid_mux.sh` is later run as user Alfred, it fails with
`Permission denied` when trying to overwrite that file. Fix:
```bash
sudo rm /tmp/scanbox_cameras.env
./rebuild_vid_mux.sh
```
Long-term fix: write the env file to a project-owned location or always run
the script with the same user.

---

## Section 3 — v4l2loopback & Kernel Modules

**Three host mounts required for out-of-tree module compilation**
The kernel header symlink chain on Raspberry Pi OS requires three separate
mounts into the build container:
- `/lib/modules` — module tree, contains the `build` symlink
- `/usr/src` — the actual `linux-headers-*` sources
- `/usr/lib/linux-kbuild-<ver>` — the `scripts/` directory, which lives
  outside `/usr/src` and is reached via a dangling symlink otherwise

Omitting the third mount produces: `scripts/Kbuild.include: No such file or directory`

Resolve the kbuild dir dynamically (version-agnostic):
```bash
KBUILD_DIR=$(dirname $(readlink -f /lib/modules/$(uname -r)/build/scripts))
```

**v4l2loopback must match host kernel gcc version**
Using `ubuntu:22.04` (gcc-11) as the build base when the host kernel was
compiled with gcc-14 causes build failures. Use `debian:trixie` which ships
gcc-14 natively.

**v4l2loopback `video_nr=200` forces deterministic device node**
Without `video_nr=200`, the kernel assigns the next available index which may
collide with physical cameras. Always force a high index to avoid conflicts.

---

## Section 4 — Multi-Camera & Device Management

**Docker cannot add devices to a running container**
Device bindings are resolved at `docker run` time via Linux namespaces and
cannot be modified afterwards. Hot-plug support therefore requires either
restarting the container or pre-mapping a fixed number of device slots.

**Camera slot assignment must be deterministic**
Physical cameras are assigned to slots `/dev/video100`–`/dev/video103` based
on their order in `/dev/v4l/by-id/`. This order depends on the USB port the
camera is connected to, not the camera model. If cameras are swapped between
ports after the container starts, the slot assignment will not match.

**`SCANBOX_SOURCES` env var must be passed at `docker run` time**
The Flask API and GStreamer pipeline both read `SCANBOX_SOURCES` at startup.
Changing it after the container is running has no effect. A restart is required
to pick up a new camera configuration.

---

## Section 5 — Host OS & Systemd

**USB gadget (dwc2) configuration must live on the host**
The `dwc2` driver and `configfs` gadget configuration require direct kernel
and hardware access. They cannot be containerized. This is the one explicit
exception to the project's strict containerization rule.

**Raspberry Pi 4 USB-C port supports gadget mode**
The USB-C port on the Pi 4 uses the `dwc2` controller which supports both
host and device (gadget) modes. It is the correct port for CDC-ECM or UVC
gadget output. The USB-A ports do not support gadget mode.

**VBUS must be cut when powering Pi from GPIO via USB-C data cable**
If the Pi is powered externally via GPIO pins and also connected to a PC via
USB-C for data, the VBUS line must be cut on the USB cable to prevent
back-powering conflicts between the two power sources.

---

## Section 6 — Development Workflow

**Claude Code agent consumes tokens rapidly in autonomous mode**
When given open-ended instructions, the agent reads all files, iterates, and
verifies repeatedly — consuming quota in minutes. Always give the agent a
single, concrete, bounded task. Stop it immediately if it appears to be
thinking without executing.

**`/compact` in Claude Code reduces context but costs tokens**
Running `/compact` summarizes the session context and reduces per-call token
usage going forward. However, the compaction itself is an expensive call.
Use it after long sessions, not routinely.

**`git push --force` required after `git commit --amend` on pushed commits**
VS Code's Source Control panel does not expose "Push Force" directly. Use the
terminal:
```bash
git push --force origin main
```
Safe to use when working alone on a private repository.

---

## Section 7 — Web UI & JavaScript

**A JS crash at init silently prevents all async initialization** *(2026-06-01)*
If any synchronous code in a `<script>` block throws an uncaught exception,
execution stops immediately — no `fetchStatus()`, no `loadControls()`, no source
list rendering. The UI appears empty with no obvious error unless the browser
console is open.

In this project: `setFocus(0)` and `setZoom(0)` were called at page load and
both tried to read `.style` on a DOM element that didn't exist (`focus-fill`,
`zoom-fill`). The crash happened before any network call was made.

Always add null guards when reading `.style` or other properties of potentially
absent elements:
```javascript
function setFocus(delta) {
    focusLevel = Math.max(0, Math.min(100, focusLevel + delta));
    const el = document.getElementById('focus-fill');
    if (el) el.style.width = focusLevel + '%';
}
```
When debugging an empty UI, open the browser console first — a one-line JS error
will be far more informative than inspecting the API.

**JS module split: globals must be declared before any module that reads them** *(2026-06-01)*
When the inline `<script>` was split into 7 separate files loaded via `<script src>` tags,
`ui.js` contained two top-level calls — `setZoom(0)` and `setFocus(0)` — that read
`zoomLevel` and `focusLevel`. Those globals are declared with `let` in `main.js`,
which loads *after* `ui.js`. The `ReferenceError` aborted `ui.js` before it could
register the `keydown` and `wheel` event listeners, silently breaking all keyboard shortcuts.

Fix: moved the two init calls to `main.js`, after the global declarations. Rule: no
top-level code in any JS module may read a global that is declared in a later-loaded module.
In this project `main.js` always loads last and owns all shared globals.

---

## Section 8 — Boot Reliability & Host Provisioning

**`docker run` without `--restart` leaves dead containers forever** *(2026-06-01)*
If `vid_mux` crashes at startup (e.g. `/dev/video100` not ready, or a transient GStreamer pipeline error), the container stays in `Exited` state and never recovers. With `--restart=on-failure`, Docker auto-restarts it with exponential backoff. Apply to all critical containers.

**Docker healthcheck does not exist when using `docker run` instead of compose** *(2026-06-01)*
`rebuild_vid_mux.sh` launches `vid_mux_test` with `docker run`, not `docker compose`. The healthcheck defined in `docker-compose.yml` is ignored, so `container_healthy vid_mux_test` never returns true. Instead, verify the process directly with `docker exec vid_mux_test sh -c 'ps aux | grep "[m]ock_streamer"'`.

**`setup_usb_gadget.sh` does not auto-update when the source file is edited** *(2026-06-01)*
`host/setup_host.sh` copies `setup_usb_gadget.sh` to `/usr/local/sbin/` via `install`. If the source file is edited after initial installation, it must be copied manually:
```bash
sudo cp host/setup_usb_gadget.sh /usr/local/sbin/setup_usb_gadget.sh
```
If the gadget is already active, it must be torn down first to bypass the idempotency guard:
```bash
echo "" | sudo tee /sys/kernel/config/usb_gadget/scanbox/UDC
sudo rm -rf /sys/kernel/config/usb_gadget/scanbox
sudo /usr/local/sbin/setup_usb_gadget.sh
```

**Configfs is not always mounted on minimal distros** *(2026-06-01)*
On full Raspberry Pi OS, systemd mounts `configfs` automatically. On minimal distros (debootstrap --variant=minbase), it does not. `setup_usb_gadget.sh` needs `/sys/kernel/config` to create the USB gadget. `setup_host.sh` now mounts it explicitly and adds it to `/etc/fstab`.

**`udev` is absent on minimal distros — required for `/dev/v4l/by-id/`** *(2026-06-01)*
`rebuild_vid_mux.sh` scans `/dev/v4l/by-id/*-video-index0` to detect cameras. On distros without udev (or using busybox-mdev instead), these symlinks do not exist and no cameras are found. `setup_host.sh` now installs `udev` explicitly.

**`procps` is absent on minimal distros — `ps` fails** *(2026-06-01)*
`vid_mux_test` health verification in `rebuild_vid_mux.sh` uses `ps aux | grep "[m]ock_streamer"`. On distros without procps (or with busybox ps), the command either does not exist or its output format is incompatible. `setup_host.sh` now installs `procps` explicitly.

**The systemd `scanbox.service` can become outdated** *(2026-06-01)*
If `rebuild_vid_mux.sh` is moved from `scripts/` to the project root (or any path change), the service at `/etc/systemd/system/` still points to the old path. `host/setup_host.sh` reinstalls it, but on an already-configured Pi it must be copied manually:
```bash
sudo cp host/scanbox.service /etc/systemd/system/scanbox.service
sudo systemctl daemon-reload
```
Symptom: `journalctl -u scanbox.service` shows `Unable to locate executable`.

**Docker images are cached — changing a config file requires a rebuild** *(2026-06-01)*
`scanbox_dhcp` copies `dnsmasq.conf` inside the image at build time. If `dnsmasq.conf` is edited afterwards, the running container keeps using the old version. Force a rebuild:
```bash
docker build -t scanbox-scanbox_dhcp scanbox_dhcp/
docker rm -f scanbox_dhcp
docker run -d --name scanbox_dhcp --network=host --cap-add=NET_ADMIN --restart=always scanbox-scanbox_dhcp
```
The same applies to all containers with baked-in (not bind-mounted) configurations.

**Camera name detection: many cameras do not expose a USB product string** *(2026-06-01)*
`/sys/class/video4linux/videoN/name` is not available for devices mapped to high-index
slots (`video100`, `video101`) that do not appear in the container's sysfs. Use
`v4l2-ctl -d <dev> --info` and parse the `Card type` line instead.

Even then, some cameras (confirmed: Logitech `046d:0809`) have no string in the USB
`product` descriptor (`/sys/bus/usb/devices/<port>/product` is empty) and report only
`UVC Camera (046d:0809)` as their card type. There is no better name available from the
kernel or V4L2 for these devices without an external USB ID database (not installed).

The detection chain in `api.py._get_camera_card_name()` is:
1. sysfs `name` file → 2. `v4l2-ctl --info` Card type → 3. label-derived fallback.
A planned opt-in override: add a `name` field to `SCANBOX_SOURCES` entries so the
orchestrator can supply a human-readable name for cameras with poor firmware strings.