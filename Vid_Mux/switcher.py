#!/usr/bin/env python3
# switcher.py — Per-source GStreamer pipeline manager for Vid_Mux.
#
# Each source runs in its own Gst.Pipeline. Only the active source's appsink
# writes frames to frame_queue; the rest run silently (keeping their USB link
# live) or are stopped entirely (when all cameras are in idle state).
#
# Public API (called by api.py):
#   start_source(id)   — start one camera pipeline
#   stop_source(id)    — stop one camera pipeline (closes V4L2 device)
#   start_all()        — start all configured sources
#   stop_all()         — stop all running pipelines
#   switch_source(id)  — change which source feeds frame_queue
#   get_active_source() / get_running_sources()
#   run()              — init GStreamer, block the caller's thread forever

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
# Source list
# ---------------------------------------------------------------------------

def _load_sources() -> list[dict]:
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

    log.warning("SCANBOX_SOURCES not set or invalid — auto-detecting devices.")
    sources = []
    if os.path.exists("/dev/video100"):
        sources.append({"id": 0, "slot": "/dev/video100", "label": "video100"})
    else:
        log.error("Auto-detection: /dev/video100 not found.")
        sys.exit(1)
    if os.path.exists("/dev/video200"):
        sources.append({"id": 1, "slot": "/dev/video200", "label": "mock"})
    else:
        sources.append({"id": 1, "slot": None, "label": "mock"})
    return sources


SOURCES: list[dict] = _load_sources()
VALID_IDS: set[int] = {s["id"] for s in SOURCES}

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_lock          = threading.Lock()
_active_source = SOURCES[0]["id"] if SOURCES else 0
_pipelines: dict[int, Gst.Pipeline] = {}

# Frame queue — consumed by api.py to serve the MJPEG stream.
frame_queue: queue.Queue = queue.Queue(maxsize=2)

# ---------------------------------------------------------------------------
# Appsink callback factory
# ---------------------------------------------------------------------------

def _make_callback(source_id: int):
    """Return a new-sample handler that only enqueues frames from the active source."""
    def on_new_sample(appsink):
        with _lock:
            is_active = (_active_source == source_id)
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        if not is_active:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if ok:
            data = bytes(mapinfo.data)
            buf.unmap(mapinfo)
            if frame_queue.full():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    pass
            frame_queue.put_nowait(data)
        return Gst.FlowReturn.OK
    return on_new_sample

# ---------------------------------------------------------------------------
# Pipeline construction
# ---------------------------------------------------------------------------

def _build_pipeline_for(source: dict) -> str:
    slot  = source.get("slot")
    label = source.get("label", "")
    is_mock = (not slot) or (label == "mock") or label.startswith("mock_")

    sink = "appsink name=output emit-signals=true max-buffers=2 drop=true sync=false"

    if is_mock:
        if label in ("mock", "mock_0"):
            pattern, text = "colors", "MOCK 1: "
        else:
            pattern, text = "smpte", "MOCK 2: "
        return (
            f"videotestsrc pattern={pattern} "
            f"! video/x-raw,width=640,height=480,framerate=30/1 "
            f"! timeoverlay halignment=left valignment=bottom "
            f"text=\"{text}\" shaded-background=true "
            f"! videoconvert ! video/x-raw,format=I420 "
            f"! jpegenc quality=85 ! {sink}"
        )
    else:
        # Physical USB camera: capture MJPEG natively and pass straight through.
        return (
            f"v4l2src device={slot} "
            f"! image/jpeg,width=640,height=480,framerate=30/1 "
            f"! {sink}"
        )

# ---------------------------------------------------------------------------
# Public pipeline management
# ---------------------------------------------------------------------------

def start_source(source_id: int) -> bool:
    """Start the pipeline for one source. No-op if already running."""
    with _lock:
        if source_id in _pipelines:
            return True

    source = next((s for s in SOURCES if s["id"] == source_id), None)
    if source is None:
        log.error("start_source: unknown id %d", source_id)
        return False

    desc = _build_pipeline_for(source)
    log.info("Starting source %d ...", source_id)
    pipeline = Gst.parse_launch(desc)

    appsink = pipeline.get_by_name("output")
    appsink.connect("new-sample", _make_callback(source_id))

    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        log.error("Source %d: pipeline failed to start.", source_id)
        pipeline.set_state(Gst.State.NULL)
        return False
    if ret == Gst.StateChangeReturn.ASYNC:
        # Wait up to 5 s for the pipeline to reach PLAYING.
        pipeline.get_state(5 * Gst.SECOND)

    with _lock:
        _pipelines[source_id] = pipeline
    log.info("Source %d running.", source_id)
    return True


def stop_source(source_id: int) -> bool:
    """Stop and destroy the pipeline for one source (closes V4L2 device)."""
    with _lock:
        pipeline = _pipelines.pop(source_id, None)
    if pipeline is None:
        return True
    pipeline.set_state(Gst.State.NULL)
    log.info("Source %d stopped.", source_id)
    return True


def switch_source(source_id: int) -> bool:
    """Switch which source feeds frame_queue. Starts the source if not running."""
    global _active_source
    if source_id not in VALID_IDS:
        log.error("Invalid source_id %d (valid: %s)", source_id, sorted(VALID_IDS))
        return False
    with _lock:
        already = source_id in _pipelines
    if not already:
        if not start_source(source_id):
            return False
    with _lock:
        _active_source = source_id
    log.info("Switched to source %d.", source_id)
    return True


def get_active_source() -> int:
    with _lock:
        return _active_source


def start_all():
    """Start pipelines for all configured sources."""
    for s in SOURCES:
        start_source(s["id"])


def stop_all():
    """Stop all running pipelines."""
    for sid in list(_pipelines.keys()):
        stop_source(sid)


def get_running_sources() -> list[int]:
    with _lock:
        return list(_pipelines.keys())

# ---------------------------------------------------------------------------
# Entry point (called from main.py in a background thread)
# ---------------------------------------------------------------------------

def run():
    """Initialise GStreamer. Cameras are started by the first client heartbeat."""
    Gst.init(None)
    log.info("Loaded %d source(s). Waiting for first client heartbeat.", len(SOURCES))
    # Block this thread forever — GStreamer streaming runs in its own threads.
    threading.Event().wait()
