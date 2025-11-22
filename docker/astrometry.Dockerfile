# Base image for astrometry.net solver (CPU-only)
FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    astrometry.net \
    ca-certificates \
    python3 \
    python3-pip \
    netpbm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Expect index files mounted at /data/indexes
ENV ASTROMETRY_INDEX_DIR=/data/indexes

COPY app/services/solver.py /app/services/solver.py
ENTRYPOINT ["/bin/bash"]
