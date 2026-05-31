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
|   |   [ /dev/video10 ] ──(Loopback Driver)──┐         |                                          |
|   +─────────────────────────────────────────┼─────────+                                          |
|                                             │                                                    |
|   +─────────────────────────────────────────┼────────────────────────────────────────────────+   |
|   | Vid_Mux Container (Main Application)    │                                                |   |
|   |                                         ▼                                                |   |
|   |   [ GStreamer Switcher Pipeline ]                                                        |   |
|   |   sink_0 (/dev/video0)  ───┐                                                             |   |
|   |                            ├──► [ input-selector ] ──► [ Output Streamer ]               |   |
|   |   sink_1 (/dev/video10) ───┘           ▲                      │                          |   |
|   |                                        │                      │                          |   |
|   |                                  (Switch Pad)                 │                          |   |
|   |                                        │                      │ (Network Stream)         |   |
|   |   [ Signaling Control Service ]        │                      │  RTP / H264              |   |
|   |   Python REST API (Port 5000) ─────────┘                      │  UDP Port 9000           |   |
|   +───────────────────────────────────────────────────────────────┼──────────────────────────+   |
|                                                                   │                              |
|   +───────────────────────────────────────────────────────────────┼──────────────────────────+   |
|   | HOST OS LAYER                                                 │                          |   |
|   |                                                               │                          |   |
|   |   [ /dev/video0 ] ────────────────────────────────────────────┘                          |   |
|   |   (Physical USB Webcam Hardware Interface)                    │                          |   |
|   |                                                               │                          |   |
|   |   [ Hardware Keyboard Events ] ──► [ Host Input Daemon ]      │                          |   |
|   |   (/dev/input/eventX)               (Listens to Keypress)     │                          |   |
|   |                                               │               │                          |   |
|   |                                               ▼ (HTTP POST)   │                          |   |
|   |                                         localhost:5000        │                          |   |
+───┼───────────────────────────────────────────────┼───────────────┼──────────────────────────+
    │                                               │               │
    │ LOCAL NETWORK (LAN / WLAN)                    │               │
    ▼                                               ▼               ▼
+────────────────────────────────────────────────────────────────────────────────────────────────--+

| DEVELOPMENT STATION (PC)                                                                         |
|                                                                                                  |
|   [ Manual Testing Tools ] ───────────────────────┘               │                          |
|   Command Line: curl / HTTP Requests                              │                          |
|                                                                   ▼                          |
|   [ Video Monitoring Suite ] ◄────────────────────────────────────┘                          |
|   Low-Latency Player: gst-launch-1.0 / ffplay (Port 9000)                                        |
+--------------------------------------------------------------------------------------------------+
```

## 2. Component Specifications

### A. Andamiaje Container: `Vid_Mux_TEST`
This container encapsulates the hardware simulation tools and will be destroyed once the integration phase is complete. Its design strictly follows the principle of **zero permanent modifications to the Host Operating System**.
*   **Virtual Device Injection:** The container includes, compiles, or loads the `v4l2loopback` kernel module. Upon startup, it interacts with the Linux video subsystem to instantiate a video loopback device on the host, assigning it the static node `/dev/video10`.
*   **Synthetic Stream Generator:** To ensure that frame drops, freezes, or refresh issues can be measured during switching, it constantly feeds video data into `/dev/video10`.
*   **Video Pipeline:** Uses `videotestsrc` to generate a standardized color bar pattern (SMPTE) with an overlapping millisecond-precision clock (`timeoverlay`) to facilitate visual latency analysis.

### B. Main Application Container: `Vid_Mux`
This is the core software module under test. It manages the real execution business logic and remains agnostic of whether the input block devices are physical sensors or loopback devices.
*   **Video Ingestion:** Maps and reads from `/dev/video0` (Physical Camera) and `/dev/video10` (Virtual Test Camera).
*   **Video Switching:** Evaluates the inputs using GStreamer's `input-selector` node.
*   **API Control:** Runs a Python server on port `5000` to process JSON commands and execute state machine transitions.

---

## 3. Network Configuration and V4L2 Device Mapping

To ensure clean and transparent interaction between both containers, the following Docker deployment guidelines apply:

*   **Network Mode:** Both containers will run under the `--network=host` parameter. This allows sharing the local socket space of the Raspberry Pi, facilitating signaling and internal loopback traffic.
*   **V4L2 Device Mapping:**
    *   `Vid_Mux` (Application): Requires read access to nodes `--device=/dev/video0` and `--device=/dev/video10`.
    *   `Vid_Mux_TEST` (Mocking): Requires extended privileges (`--privileged`) or specific kernel capabilities (`CAP_SYS_MODULE`) exclusively to instantiate the virtual device controller into the shared kernel space.

---

## 4. Visual Verification Protocol (Success Criteria)

Verification of correct operation will be performed remotely from the workstation (Development PC) connected to the same local network as the Raspberry Pi:

1.  **Output Stream Monitoring:** The development PC will run a low-latency media player (`gst-launch-1.0` or `ffplay`) listening to the UDP stream emitted on port `9000` by the Raspberry Pi.
2.  **Signaling Injection:** Test `curl` commands or host keyboard events will be executed, simulating the project manager's requested keyboard shortcuts.
3.  **Acceptance Metrics:**
    *   The video stream on the development PC must never close, drop connections, or blink to a black screen during switching.
    *   The image transition between the real webcam (`/dev/video0`) and the bars with the clock (`/dev/video10`) must visually complete immediately (< 200ms).
