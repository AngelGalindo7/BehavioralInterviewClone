# ── Stage 1: compile Python C-extensions ─────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /app

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip wheel --quiet && \
    pip install --no-cache-dir -r requirements.txt --quiet

# ── Stage 2: lean runtime image ───────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# libjemalloc2: prevents memory fragmentation during PCM audio streaming
# curl: required by HEALTHCHECK
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends libjemalloc2 curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY alembic.ini .
COPY app/ ./app/
COPY ingestion/ ./ingestion/
COPY data/ ./data/

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# jemalloc path for Ubuntu/Debian ARM64 (Graviton2) — overridable via env_file
ENV LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libjemalloc.so.2

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--log-level", "info", \
     "--access-log"]
