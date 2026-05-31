#!/usr/bin/env python3
# switcher.py — GStreamer video switcher pipeline for Vid_Mux.
#
# Reads from two V4L2 sources simultaneously:
#   sink_0: /dev/video100 (physical USB camera)
#   sink_1: /dev/video200 (synthetic mock camera from Vid_Mux_TEST)
#
# Uses GStreamer input-selector to hot-swap between sources without
# interrupting the output stream.
#
# Output: JPEG frames pushed into a thread-safe queue, consumed by
# api.py to serve a live MJPEG stream over HTTP (/stream).

import gi
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
# Shared state
# ---------------------------------------------------------------------------
_lock           = threading.Lock()
_active_source  = 0
_selector_el    = None

# Frame queue — api.py reads from this to serve the MJPEG stream.
# maxsize=2 keeps latency low; old frames are dropped if the consumer is slow.
frame_queue: queue.Queue = queue.Queue(maxsize=2)

# ---------------------------------------------------------------------------
# Public interface (called by api.py in the same process)
# ---------------------------------------------------------------------------

def switch_source(source_id: int) -> bool:
    global _active_source
    if source_id not in (0, 1):
        log.error("Invalid source_id %s", source_id)
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
# Pipeline
# ---------------------------------------------------------------------------

_PIPELINE_SRC1_REAL = """
    v4l2src device=/dev/video200 name=src1 io-mode=rw
        ! video/x-raw,width=640,height=480,framerate=30/1
        ! videoconvert ! video/x-raw,format=I420
        ! queue name=q1 ! selector.sink_1
"""

# Fallback used in production when /dev/video200 is absent (vid_mux_test not running).
# videotestsrc generates a synthetic SMPTE colour-bar pattern so the two-source
# switching UI remains functional without a real second device.
_PIPELINE_SRC1_MOCK = """
    videotestsrc pattern=smpte name=src1
        ! video/x-raw,width=640,height=480,framerate=30/1,format=I420
        ! queue name=q1 ! selector.sink_1
"""

_PIPELINE_TEMPLATE = """
    v4l2src device=/dev/video100 name=src0
        ! image/jpeg,width=640,height=480,framerate=30/1
        ! jpegdec ! videoconvert ! video/x-raw,format=I420
        ! queue name=q0 ! input-selector name=selector

    {src1}

    selector.
        ! jpegenc quality=85
        ! appsink name=output emit-signals=true max-buffers=2 drop=true sync=false
"""

import os as _os

def _build_pipeline_desc() -> str:
    if _os.path.exists("/dev/video200"):
        log.info("video200 present — using real mock source (Vid_Mux_TEST).")
        return _PIPELINE_TEMPLATE.format(src1=_PIPELINE_SRC1_REAL)
    log.warning("video200 not found — falling back to synthetic SMPTE source for sink_1.")
    return _PIPELINE_TEMPLATE.format(src1=_PIPELINE_SRC1_MOCK)

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
    log.info("Building GStreamer pipeline...")

    pipeline = Gst.parse_launch(_build_pipeline_desc())

    _selector_el = pipeline.get_by_name("selector")
    if _selector_el is None:
        log.error("input-selector not found in pipeline.")
        sys.exit(1)

    # Set initial pad to sink_0 (physical camera)
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