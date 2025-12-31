FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY app /app/app

RUN python -m pip install --no-cache-dir . requests beautifulsoup4

CMD ["sleep", "infinity"]
