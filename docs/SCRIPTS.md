# SCANBOX — Scripts Reference

Host provisioning scripts live in `host/`. The runtime operational script (`rebuild_vid_mux.sh`) lives at the project root. Everything else runs inside containers.

---

## host/setup_host.sh

**Purpose:** One-time host provisioning for a fresh Raspberry Pi.

Installs the bare minimum that must exist on the host OS:
- Docker Engine + compose plugin
- Kernel headers (`linux-headers-<running-kernel>`) — required so Vid_Mux_TEST can compile v4l2loopback inside its container against the live kernel
- Essential host utilities: `kmod` (modprobe/lsmod), `procps` (ps), `udev` (by-id symlinks) — for minimal distro compatibility
- `git`
- configfs mount in `/etc/fstab` — required for USB gadget setup; systemd doesn't always mount it in minimal distros
- Copies `host/scanbox-gadget.service` and `host/scanbox.service` to `/etc/systemd/system/` and enables them

Also generates `.env` with `KBUILD_DIR=<path>` so `rebuild_vid_mux.sh` and `Vid_Mux_TEST` know where the kbuild scripts live.

**Usage:**
```bash
sudo ./host/setup_host.sh
sudo reboot
```

Idempotent — safe to re-run after a kernel update to refresh `KBUILD_DIR` and reinstall headers.

**Nothing else belongs here.** GStreamer, Python, v4l-utils, build toolchain, v4l2loopback source — all of that is containerised.

---

## host/setup_usb_gadget.sh

**Purpose:** Configure the USB NCM network gadget on the Pi 4 USB-C port.

Must run as root. Called automatically at boot by `host/scanbox-gadget.service` (which runs before Docker). Safe to run manually — fully idempotent.

What it does:
1. Loads `libcomposite` and `usb_f_ncm` kernel modules
2. Tears down any existing gadget at `/sys/kernel/config/usb_gadget/scanbox`
3. Creates a new NCM gadget definition in configfs
4. Binds it to the `fe980000.usb` UDC (Pi 4 USB-C port)
5. Assigns `192.168.199.1/30` to the resulting `usb0` interface

Why it must be on the host: configfs is a kernel-space filesystem. Manipulating it requires root and direct access to `/sys` — not possible from inside a container without full privilege + host kernel namespace sharing, which defeats the security model.

**Usage:**
```bash
sudo ./host/setup_usb_gadget.sh
```

**Network result:**
| Side | IP |
|---|---|
| Pi (device) | 192.168.199.1 |
| Windows PC (host) | 192.168.199.2 (DHCP via scanbox_dhcp, /30 point-to-point) |

---

## rebuild_vid_mux.sh

**Purpose:** Full stack lifecycle manager — detects cameras, builds the vid_mux image, and launches all containers.

This is the primary operational script. It is called at every boot by `host/scanbox.service` and should also be called manually after code changes or when a new camera is plugged in.

**Boot flow:**
1. **scanbox_dhcp** — start if not already running (DHCP server on usb0); `--restart=always`
2. **vid_mux_test** — start if not already healthy; builds the image if missing; passes the three kernel header mounts needed to compile v4l2loopback; `--restart=always`
3. **Wait for /dev/video200** — polls every 2s, timeout 120s; exits with error if it never appears
4. **Wait for mock_streamer** — polls `docker exec vid_mux_test ps aux` for the mock_streamer process (up to 60s); the container has no Docker healthcheck when launched via `docker run`
5. **Camera discovery** — scans `/dev/v4l/by-id/*-video-index0`; assigns deterministic slots `video100`…`video103` (max 4 physical cameras)
6. **Build SCANBOX_SOURCES** — JSON array `[{"id":N,"slot":"/dev/videoN","label":"..."}]` + `--device` flags; writes `/tmp/scanbox_cameras.env`
7. **Stop, rebuild, relaunch vid_mux** — always rebuilds from source; `--restart=on-failure`; passes `SCANBOX_SOURCES` as env var; mounts `snapshots/` as `/exports/snapshots`

**Usage:**
```bash
# From the project root:
./rebuild_vid_mux.sh
```

**Output:** prints `SCANBOX_SOURCES` JSON and the Web UI URL on success.

**Key design decision:** vid_mux is always rebuilt (not reused) so code changes take effect immediately. scanbox_dhcp and vid_mux_test are skipped if already healthy to keep boot time short.

---

## scripts/capture_test.sh

**Purpose:** Diagnostic tool — captures a single JPEG frame from the mock or physical camera directly, bypassing the vid_mux pipeline.

Useful for verifying that a camera device is readable before starting the full stack, or for debugging capture issues in isolation. Spawns a temporary container using the `vid_mux_test` image with GStreamer installed.

**Usage:**
```bash
./scripts/capture_test.sh --mock          # capture from /dev/video200
./scripts/capture_test.sh --real          # capture from physical USB camera (auto-detected)
./scripts/capture_test.sh --mock --real   # capture from both
```

Output files are saved to `scripts/` with a timestamp prefix, e.g. `scripts/2026_06_01__12_00_00_mock_cam.jpg`.

**Requirements:** `vid_mux_test` Docker image must exist (`docker build -t vid_mux_test Vid_Mux_TEST/` or run `rebuild_vid_mux.sh` once). `/dev/video200` must exist for `--mock`.
