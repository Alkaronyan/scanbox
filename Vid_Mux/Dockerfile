# Vid_Mux — Production Video Switcher Container
# Base: debian:trixie (matches host toolchain gcc-14, consistent with Vid_Mux_TEST)
FROM debian:trixie

# Install GStreamer, Python3, Flask, and GObject introspection bindings
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-flask \
    python3-gi \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/vid_mux

COPY entrypoint.sh .
COPY main.py .
COPY switcher.py .
COPY api.py .
COPY templates/ templates/
COPY static/ static/

RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
