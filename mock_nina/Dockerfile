FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MOCK_NINA_DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/

RUN pip install --no-cache-dir \
      fastapi~=0.115 \
      uvicorn[standard]~=0.30 \
      pydantic-settings~=2.4 \
      numpy~=2.1 \
      astropy~=6.1

COPY mock_nina /app/mock_nina

VOLUME ["/data"]

EXPOSE 1888

CMD ["uvicorn", "mock_nina.main:app", "--host", "0.0.0.0", "--port", "1888"]
