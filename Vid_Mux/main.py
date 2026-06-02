#!/usr/bin/env python3
# main.py — Entry point for Vid_Mux.
# Launches the GStreamer pipeline in a background thread and
# the Flask API in the main thread, sharing state in the same process.

import threading
import logging
import switcher
import api

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

# Start GStreamer pipeline in a background thread
t = threading.Thread(target=switcher.run, daemon=True)
t.start()

# Start the frame refresher — keeps _last_frame always current so the
# GStreamer appsink never stalls due to backpressure from an idle queue.
api.start_frame_refresher()
api.start_camera_watchdog()

# Run Flask API in the main thread (blocks)
api.app.run(host="0.0.0.0", port=80, debug=False)