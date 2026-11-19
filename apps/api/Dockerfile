# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv

WORKDIR /app
RUN uv venv /opt/venv

COPY packages/shared-py/pyproject.toml packages/shared-py/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml

# Install third-party dependencies separately from source so code edits keep Docker cache intact
RUN mkdir -p packages/shared-py/src/vxg_shared apps/api/src/vxg_api && \
    touch packages/shared-py/src/vxg_shared/__init__.py apps/api/src/vxg_api/__init__.py && \
    uv pip install --python /opt/venv/bin/python --no-cache \
        ./packages/shared-py ./apps/api && \
    uv pip uninstall --python /opt/venv/bin/python vxg-shared vxg-api

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/packages/shared-py/src:/app/apps/api/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Ho_Chi_Minh

RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY packages/shared-py/alembic.ini /app/packages/shared-py/alembic.ini
COPY packages/shared-py/alembic /app/packages/shared-py/alembic
COPY packages/shared-py/src /app/packages/shared-py/src
COPY apps/api/src /app/apps/api/src

RUN chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; port = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3)"

CMD ["sh", "-c", "exec uvicorn vxg_api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
