# Experimental CLEAN Linux runtime, not a distributable container or application.
# APT/network access is permitted during image construction ONLY.
# Execution must pass Docker --network none, with no Python installed.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 libgl1 libopengl0 libxkbcommon-x11-0 \
    libxcb-cursor0 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xkb1 libxcb-icccm4 \
    libxcb-shape0 libfontconfig1 libdbus-1-3 \
 && rm -rf /var/lib/apt/lists/*
RUN ! command -v python && ! command -v python3 && ! command -v pip
RUN mkdir -p /home/revisionworx && chown 10001:10001 /home/revisionworx
USER 10001:10001
WORKDIR /home/revisionworx
ENV HOME=/tmp \
    XDG_CONFIG_HOME=/tmp/config \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_RUNTIME_DIR=/tmp/runtime \
    QT_QPA_PLATFORM=offscreen
