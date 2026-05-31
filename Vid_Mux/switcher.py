#!/usr/bin/env python3
# switcher.py — GStreamer video switcher pipeline for Vid_Mux.
#
# Reads from N V4L2 sources simultaneously (determined by SCANBOX_SOURCES env var).
# Physical cameras use MJPEG capture format; mock camera (/dev/video200) uses io-mode=rw.
#
# Uses GStreamer input-selector to hot-swap between sources without
# interrupting the output stream.
#
# Output: JPEG frames pushed into a thread-safe queue, consumed by
# api.py to serve a live MJPEG stream over HTTP (/stream).

import gi
import os
import json
import threading
import queue
import logging
import sys

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib

logging.basicConfig(
    level=logging.INFO,
    format='[switcher] %(asctime)s %(levelname)s: %(message)s'
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source list — loaded from SCANBOX_SOURCES env var at module load time.
# Falls back to auto-detection if env var is missing or invalid.
# Each entry: {"id": int, "slot": "/dev/videoN", "label": str}
# ---------------------------------------------------------------------------

def _load_sources() -> list[dict]:
    """Parse SCANBOX_SOURCES env var or fall back to auto-detection."""
    raw = os.environ.get("SCANBOX_SOURCES", "")
    if raw:
        try:
            sources = json.loads(raw)
            if isinstance(sources, list) and len(sources) > 0:
                log.info("Loaded %d source(s) from SCANBOX_SOURCES.", len(sources))
                for s in sources:
                    log.info("  id=%d  slot=%s  label=%s", s["id"], s["slot"], s["label"])
                return sources
        except (json.JSONDecodeError, KeyError) as e:
            log.warning("SCANBOX_SOURCES parse error (%s) — falling back to auto-detection.", e)

    # Auto-detection fallback: video100 mandatory, video200 optional
    log.warning("SCANBOX_SOURCES not set or invalid — auto-detecting devices.")
    sources = []
    if os.path.exists("/dev/video100"):
        sources.append({"id": 0, "slot": "/dev/video100", "label": "video100"})
    else:
        log.error("Auto-detection: /dev/video100 not found — cannot build pipeline.")
        sys.exit(1)

    if os.path.exists("/dev/video200"):
        log.info("Auto-detection: video200 present — including mock source.")
        sources.append({"id": 1, "slot": "/dev/video200", "label": "mock"})
    else:
        log.warning("Auto-detection: video200 not found — using SMPTE videotestsrc fallback.")
        sources.append({"id": 1, "slot": None, "label": "mock"})  # slot=None → SMPTE fallback

    return sources


SOURCES: list[dict] = _load_sources()
VALID_IDS: set[int] = {s["id"] for s in SOURCES}

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_lock           = threading.Lock()
_active_source  = SOURCES[0]["id"] if SOURCES else 0
_selector_el    = None

# Frame queue — api.py reads from this to serve the MJPEG stream.
# maxsize=2 keeps latency low; old frames are dropped if the consumer is slow.
frame_queue: queue.Queue = queue.Queue(maxsize=2)

# ---------------------------------------------------------------------------
# Public interface (called by api.py in the same process)
# ---------------------------------------------------------------------------

def switch_source(source_id: int) -> bool:
    global _active_source
    if source_id not in VALID_IDS:
        log.error("Invalid source_id %s (valid: %s)", source_id, sorted(VALID_IDS))
        return False
    with _lock:
        if _selector_el is None:
            log.error("Pipeline not ready yet.")
            return False
        pad = _selector_el.get_static_pad(f"sink_{source_id}")
        if pad is None:
            log.error("Pad sink_%s not found.", source_id)
            return False
        _selector_el.set_property("active-pad", pad)
        _active_source = source_id
    log.info("Switched to source %d.", source_id)
    return True


def get_active_source() -> int:
    with _lock:
        return _active_source

# ---------------------------------------------------------------------------
# GStreamer appsink callback — called for every output frame
# ---------------------------------------------------------------------------

def _on_new_sample(appsink):
    sample = appsink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK
    buf = sample.get_buffer()
    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if ok:
        data = bytes(mapinfo.data)
        buf.unmap(mapinfo)
        # Drop the oldest frame if the consumer hasn't caught up
        if frame_queue.full():
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                pass
        frame_queue.put_nowait(data)
    return Gst.FlowReturn.OK

# ---------------------------------------------------------------------------
# Pipeline building
# ---------------------------------------------------------------------------

def _build_source_segment(source: dict, sink_index: int) -> str:
    """Return the GStreamer pipeline fragment for one source."""
    slot = source.get("slot")
    label = source.get("label", "")

    # Determine if this is the mock loopback device
    is_mock = (slot == "/dev/video200") or (label == "mock")

    if slot is None:
        # SMPTE videotestsrc fallback (used when video200 is absent in fallback mode)
        return (
            f"videotestsrc pattern=smpte name=src{sink_index}\n"
            f"    ! video/x-raw,width=640,height=480,framerate=30/1,format=I420\n"
            f"    ! queue name=q{sink_index} max-size-buffers=2 leaky=downstream ! selector.sink_{sink_index}"
        )
    elif is_mock:
        # Mock camera: use videotestsrc directly inside the pipeline.
        # Reading from /dev/video200 (v4l2loopback) via v4l2src fails because
        # v4l2loopback rejects CAPTURE-side S_FMT while the OUTPUT side (mock_streamer)
        # has the device open. videotestsrc bypasses this entirely.
        return (
            f"videotestsrc pattern=colors name=src{sink_index}\n"
            f"    ! video/x-raw,width=640,height=480,framerate=30/1\n"
            f"    ! timeoverlay halignment=left valignment=bottom"
            f" text=\"MOCK: \" shaded-background=true\n"
            f"    ! videoconvert ! video/x-raw,format=I420\n"
            f"    ! queue name=q{sink_index} max-size-buffers=2 leaky=downstream ! selector.sink_{sink_index}"
        )
    else:
        # Physical USB camera: outputs MJPEG natively
        return (
            f"v4l2src device={slot} name=src{sink_index}\n"
            f"    ! image/jpeg,width=640,height=480,framerate=30/1\n"
            f"    ! jpegdec ! videoconvert ! video/x-raw,format=I420\n"
            f"    ! queue name=q{sink_index} max-size-buffers=2 leaky=downstream ! selector.sink_{sink_index}"
        )


def _build_pipeline_desc() -> str:
    """Build the full GStreamer pipeline string from the SOURCES list."""
    if not SOURCES:
        log.error("No sources defined — cannot build pipeline.")
        sys.exit(1)

    segments = []
    for i, source in enumerate(SOURCES):
        # First source feeds into the input-selector definition
        if i == 0:
            seg = _build_source_segment(source, 0)
            # Replace "! selector.sink_0" with "! input-selector name=selector"
            # so the selector element is declared once at the first source
            seg = seg.replace(
                "! queue name=q0 max-size-buffers=2 leaky=downstream ! selector.sink_0",
                "! queue name=q0 max-size-buffers=2 leaky=downstream ! input-selector name=selector sync-streams=false"
            )
        else:
            seg = _build_source_segment(source, i)
        segments.append(seg)

    output = (
        "selector.\n"
        "    ! jpegenc quality=85\n"
        "    ! appsink name=output emit-signals=true max-buffers=2 drop=true sync=false"
    )

    pipeline = "\n\n".join(segments) + "\n\n" + output
    log.info("Pipeline description:\n%s", pipeline)
    return pipeline

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _on_bus_message(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.EOS:
        log.warning("EOS — stopping pipeline.")
        loop.quit()
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        log.error("GStreamer error: %s | %s", err, debug)
        loop.quit()


def run():
    global _selector_el

    Gst.init(None)
    log.info("Building GStreamer pipeline (%d source(s))...", len(SOURCES))

    pipeline = Gst.parse_launch(_build_pipeline_desc())

    _selector_el = pipeline.get_by_name("selector")
    if _selector_el is None:
        log.error("input-selector not found in pipeline.")
        sys.exit(1)

    # Set initial pad to sink_0 (first source, typically physical camera)
    pipeline.set_state(Gst.State.READY)
    initial_pad = _selector_el.get_static_pad("sink_0")
    _selector_el.set_property("active-pad", initial_pad)

    # Connect appsink callback
    appsink = pipeline.get_by_name("output")
    appsink.connect("new-sample", _on_new_sample)

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    loop = GLib.MainLoop()
    bus.connect("message", _on_bus_message, loop)

    log.info("Setting pipeline to PLAYING...")
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        log.error("Failed to set pipeline to PLAYING.")
        sys.exit(1)

    log.info("Pipeline running. Frames available via frame_queue.")

    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
        log.info("Pipeline stopped.")


if __name__ == "__main__":
    run()
