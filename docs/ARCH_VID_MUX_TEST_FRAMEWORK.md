# Testing Environment and Mocking Infrastructure Specification

This document describes the temporary testing infrastructure designed to validate the Switcher Module on a physical Raspberry Pi. It simulates the real SCANBOX project conditions without requiring the USB webcam emulator module (`configfs` / `usb_f_uvc`) or the second physical camera hardware.

## 1. System Architecture Diagram

The following schematic represents the execution isolation, data pipelines, control loops, and physical boundaries between the Raspberry Pi host hardware, the containers, and the Development PC.

```text
+--------------------------------------------------------------------------------------------------+

| RASPBERRY PI PHYSICAL HOST (Linux Kernel)                                                        |
|                                                                                                  |
|   +---------------------------------------------------+                                          |
|   | Vid_Mux_TEST Container (Mocking & Tooling)        |                                          |
|   |                                                   |                                          |
|   |   [ GStreamer Pipeline ]                          |                                          |
|   |   videotestsrc (SMPTE Bars + Clock)               |                                          |
|   |           │                                       |                                          |
|   |           ▼ (Writes Frames)                       |                                          |
|   |   [ /dev/video200 ] ──(Forced via video_nr=200)────┼┐                                        |
|   +───────────────────────────────────────────────────+│                                         |
|                                                        │                                         |
|   +────────────────────────────────────────────────────┼─────────────────────────────────────+   |
|   | Vid_Mux Container (Main Application)               │                                     |   |
|   |                                                    ▼                                     |   |
|   |   [ GStreamer Switcher Pipeline ]                                                        |   |
|   |   sink_0 (Internal /dev/video100) ◄──(Mapped via /dev/v4l/by-id/* Unique Hardware ID)    |   |
|   |   sink_1 (videotestsrc, internal)  [mock source, no device read]                        |   |
|   |               │                                                                          |   |
|   |               ▼                                                                          |   |
|   |       [ input-selector ] ──► [ Output Streamer ]                                         |   |
|   |               ▲                      │                                                   |   |
|   |               │                      │                                                   |   |
|   |         (Switch Pad)                 │                                                   |   |
|   |               │                      │ (Network Stream)                                  |   |
|   |   [ Signaling Control Service ]      │  RTP / H264                                       |   |
|   |   Python REST API (Port 80) ───────┘  UDP Port 9000                                    |   |
|   +──────────────────────────────────────────────────────────────────────────────────────────+   |
|                                                                          │                       |
|                                                                          │                       |
|   +──────────────────────────────────────────────────────────────────────┼───────────────────+   |
|   | HOST OS LAYER (Unmodified Peripherals)                                │                   |
|   |                                                                      │                   |
|   |   [ Physical USB Webcam ] ──► /dev/v4l/by-id/usb-Device_Serial_Path  │                   |
|   |                                                                      │                   |
|   |   [ Hardware Keyboard Events ] ──► [ Host Input Daemon ]              │                   |
|   |   (/dev/input/eventX)               (Listens to Keypress)            │                   |
|   |                                               │                      │                   |
|   |                                               ▼ (HTTP POST)          │                   |
|   |                                         localhost               │                   |
+───┼───────────────────────────────────────────────┼──────────────────────┼───────────────────+
    │                                               │                      │
    │ LOCAL NETWORK (LAN / WLAN)                    │                      │
    ▼                                               ▼                      ▼
+--------------------------------------------------------------------------------------------------+

| DEVELOPMENT STATION (PC)                                                                         |
|                                                                                                  |
|   [ Manual Testing Tools ] ───────────────────────┘                      │                   |
|   Command Line: curl / HTTP Requests                                     │                   |
|                                                                          ▼                   |
|   [ Video Monitoring Suite ] ◄───────────────────────────────────────────┘                   |
|   Low-Latency Player: gst-launch-1.0 / ffplay (Port 9000)                                        |
+--------------------------------------------------------------------------------------------------+
```

## 2. Component Specifications & Deterministic Mapping

### A. Andamiaje Container: `Vid_Mux_TEST`
*   **Forced Device Assignment:** The container loads the `v4l2loopback` kernel module using specific high-index runtime arguments: `modprobe v4l2loopback video_nr=200 card_label="Scanbox_Virtual_Cam"`. This configuration explicitly forces the creation of the `/dev/video200` node on the host, preventing collisions with standard devices.

### B. Main Application Container: `Vid_Mux`
*   **Immutable Hardware Mapping:** The container engine maps the host's immutable path identifier to a sandboxed high index:
    `--device=/dev/v4l/by-id/[unique-usb-serial-string]:/dev/video100`
*   **Mock source:** Vid_Mux does **not** map `/dev/video200` as a device. The mock GStreamer source uses `videotestsrc pattern=colors` + `timeoverlay` directly inside the pipeline. v4l2loopback rejects CAPTURE-side `S_FMT` from v4l2src while the OUTPUT side (Vid_Mux_TEST's v4l2sink) has the device open — this is a kernel driver constraint, not configurable. Vid_Mux_TEST still runs and keeps `/dev/video200` alive as a boot-readiness gate, but Vid_Mux never reads from it.

---

## 3. Network Configuration and V4L2 Device Mapping

> **Host prerequisites (provisioned by `setup_host.sh`):** Docker Engine, kernel
> headers (`linux-headers-*`), and git. These are the ONLY components allowed on
> the host; everything else is containerized. See the repository README for the
> containerization philosophy.

> **Required kernel volume mounts for `Vid_Mux_TEST`:** the container compiles
> `v4l2loopback` against the running host kernel, so it MUST mount THREE host
> paths read-only, following the kernel header symlink chain:
> * `-v /lib/modules:/lib/modules:ro` — module tree; `.../build` symlinks into `/usr/src`.
> * `-v /usr/src:/usr/src:ro` — the actual `linux-headers-*` sources.
> * `-v "${KBUILD_DIR}:${KBUILD_DIR}:ro"` — the kbuild `scripts` directory, which
>   lives OUTSIDE `/usr/src` in `/usr/lib/linux-kbuild-<ver>` and is reached via a
>   symlink. Resolve it version-agnostically with:
>   `KBUILD_DIR=$(dirname $(readlink -f /lib/modules/$(uname -r)/build/scripts))`.
>
> Omitting the third mount yields a dangling `scripts` symlink and a build error
> like `scripts/Kbuild.include: No such file or directory`.

To ensure clean and transparent interaction between both containers, the following Docker deployment guidelines apply:

*   **Network Mode:** Both containers will run under the `--network=host` parameter. This allows sharing the local socket space of the Raspberry Pi, facilitating signaling and internal loopback traffic.
*   **Execution Privileges:**
    *   `Vid_Mux` (Application): Standard runtime permissions with precise read-write device file flags.
    *   `Vid_Mux_TEST` (Mocking): Requires extended privileges (`--privileged`) or explicit kernel execution flags (`CAP_SYS_MODULE`) solely to trigger the loopback subsystem injection into the shared Linux kernel space.

---

## 4. Visual Verification Protocol (Success Criteria)

Verification of correct operation will be performed remotely from the workstation (Development PC) connected to the same local network as the Raspberry Pi:

1.  **Output Stream Monitoring:** The development PC will run a low-latency media player (`gst-launch-1.0` or `ffplay`) listening to the UDP stream emitted on port `9000` by the Raspberry Pi.
2.  **Signaling Injection:** Test `curl` commands or host keyboard events will be executed, simulating the project manager's requested keyboard shortcuts.
3.  **Acceptance Metrics:**
    *   The video stream on the development PC must never close, drop connections, or blink to a black screen during switching.
    *   The image transition between the real webcam (`/dev/video0`) and the bars with the clock (`/dev/video200`) must visually complete immediately (< 200ms).
