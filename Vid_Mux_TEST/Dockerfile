# Base image MUST match the host distro/toolchain: the Raspberry Pi host runs
# Debian 13 "trixie" and its kernel was built with aarch64-linux-gnu-gcc-14.
# Compiling v4l2loopback against the running kernel requires that exact compiler
# major version, so we use debian:trixie (provides gcc-14) instead of Ubuntu.
FROM debian:trixie

# Prevent interactive prompts during package installations
ENV DEBIAN_FRONTEND=noninteractive

# Install compilation tools, kernel utilities, git, and GStreamer elements
RUN apt-get update && apt-get install -y \
    build-essential \
    ca-certificates \
    kmod \
    git \
    libv4l-dev \
    python3 \
    python3-pip \
    python3-gi \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

# Test-runner dependencies — installed in a separate layer so the GStreamer
# build cache above is not invalidated when test tooling changes.
# docker.io  : provides the docker CLI so Layer 2 tests can call `docker exec`
# ffmpeg     : used by image_analysis.py for pixel-level JPEG comparison
# python3-pytest / python3-requests : pytest runner and HTTP client for the suite
RUN apt-get update && apt-get install -y \
    docker.io \
    ffmpeg \
    python3-pytest \
    python3-requests \
    && rm -rf /var/lib/apt/lists/*

# Set the internal working directory.
# NOTE: must NOT live under /usr/src — at runtime the host's /usr/src is
# bind-mounted read-only here (to expose kernel headers for v4l2loopback),
# which would shadow and lock any app directory placed under it.
WORKDIR /opt/vid_mux_test

# Copy the entrypoint deployment script and the synthetic stream generator script
COPY entrypoint.sh .
COPY mock_streamer.py .

# Grant execution permissions to the entrypoint supervisor
RUN chmod +x entrypoint.sh

# Execute the entrypoint script on startup
ENTRYPOINT ["./entrypoint.sh"]
