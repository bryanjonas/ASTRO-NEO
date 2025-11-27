FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml BUILD_NOTES.md /app/

# Create dummy app structure to allow installing dependencies
# This ensures that changes to source code don't invalidate the dependency cache
RUN mkdir -p app && touch app/__init__.py && \
    pip install --no-cache-dir .

COPY app /app/app

# Re-install the package to include the actual source code
RUN pip install --no-cache-dir .

COPY . /app
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
