FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    astrometry.net \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    netpbm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV ASTROMETRY_INDEX_DIR=/data/indexes

COPY pyproject.toml /app/pyproject.toml
COPY app /app/app
COPY app/worker/astrometry.cfg /app/astrometry.cfg
# Also drop a copy into /etc for manual CLI use
RUN cp /app/astrometry.cfg /etc/astrometry.cfg

# Isolated virtualenv to avoid Debian PEP 668 protections
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

ENV PATH="/opt/venv/bin:${PATH}"

EXPOSE 8100
CMD ["uvicorn", "app.worker.astrometry_server:app", "--host", "0.0.0.0", "--port", "8100"]
