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

# Run Flask API in the main thread (blocks)
api.app.run(host="0.0.0.0", port=5000, debug=False)