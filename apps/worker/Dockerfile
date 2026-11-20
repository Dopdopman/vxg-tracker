# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv

WORKDIR /app
RUN uv venv /opt/venv

COPY packages/shared-py/pyproject.toml packages/shared-py/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml

# Install third-party dependencies separately from source so code edits keep Docker cache intact
RUN mkdir -p packages/shared-py/src/vxg_shared apps/worker/src/vxg_worker && \
    touch packages/shared-py/src/vxg_shared/__init__.py apps/worker/src/vxg_worker/__init__.py && \
    uv pip install --python /opt/venv/bin/python --no-cache \
        ./packages/shared-py ./apps/worker && \
    uv pip uninstall --python /opt/venv/bin/python vxg-shared vxg-worker

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/packages/shared-py/src:/app/apps/worker/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Asia/Ho_Chi_Minh

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv

# Install Chromium and OS dependencies cleanly in shared path readable by non-root user
RUN apt-get update && \
    apt-get install -y --no-install-recommends libatomic1 libgomp1 ca-certificates && \
    playwright install --with-deps chromium && \
    chmod -R 755 /ms-playwright && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --home /app app

COPY packages/shared-py/src /app/packages/shared-py/src
COPY apps/worker/src /app/apps/worker/src

RUN chown -R app:app /app
USER app

CMD ["python", "-m", "vxg_worker.main"]

