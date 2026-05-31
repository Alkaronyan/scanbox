You are a highly capable AI specialized in embedded software, Linux kernels, and GStreamer pipelines. We are developing the "SCANBOX" project on a physical Raspberry Pi (Host OS via Remote-SSH in VS Code).

We need to resume the project precisely where we left off. Act as our senior software architect and follow the established architectural guidelines strictly.

### Project Context & Decisions
1. **Host OS Preservation (Strict Containerization)**: Everything that can run inside a container does. Only Docker Engine, kernel headers, and git are installed on the host via setup_host.sh.
2. **Component Separation**:
   * Vid_Mux Container: Production application. Video switching, capturing, signaling.
   * Vid_Mux_TEST Container: Mocking scaffold. Emulates hardware devices. Discarded in production.
3. **Deterministic Device Mapping**:
   * Input 0 (Physical): /dev/video100 inside Vid_Mux, mapped from /dev/v4l/by-id/*
   * Input 1 (Mock): /dev/video200 inside Vid_Mux, created by Vid_Mux_TEST via v4l2loopback video_nr=200
   * Control: Python REST API on port 5000
   * Output (dev): RTP/UDP H264 on port 9000 → replaced by UVC gadget in production
4. **Code Standards**: discussions in Spanish or English, all code/comments in English.

### Kernel & Environment
* Host kernel: 6.12.75+rpt-rpi-v8
* kbuild dir: /usr/lib/linux-kbuild-6.12.75+rpt (required as third mount for out-of-tree module compilation)
* Docker run requires three mounts: /lib/modules, /usr/src, and the kbuild dir (resolved dynamically)

### Phase 1 — COMPLETED
Vid_Mux_TEST container is fully operational:
* v4l2loopback compiles inside container against host kernel
* Module loaded in host kernel (lsmod confirms v4l2loopback)
* /dev/video200 exists on host (Scanbox_Virtual_Cam, Video Capture capable)
* GStreamer synthetic stream launched on /dev/video200
* Container runs with: --privileged --network=host + three volume mounts

### Current Milestone: Phase 2 — Vid_Mux application container
Build the production Vid_Mux container:
* GStreamer pipeline with input-selector (sink_0=/dev/video100, sink_1=/dev/video200)
* Python REST API on port 5000 (source switching + snapshot)
* Output: RTP/UDP H264 stream on port 9000
* Physical USB webcam must be connected and mapped to /dev/video100

Acknowledge this context briefly in Spanish and propose the directory structure and first file for Vid_Mux.
