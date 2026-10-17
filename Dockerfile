FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/app/.cache/huggingface \
    HF_XET_HIGH_PERFORMANCE=1

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra embeddings --no-install-project

COPY src ./src
COPY web ./web
COPY seed ./seed
RUN uv sync --frozen --no-dev --extra embeddings

COPY docker/entrypoint.sh /entrypoint.sh
RUN useradd --create-home wellwell && mkdir -p /app/data/raw /app/.cache/huggingface && chown -R wellwell /app
USER wellwell

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/api/health', method='POST'))"
ENTRYPOINT ["/entrypoint.sh"]
