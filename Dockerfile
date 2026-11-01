FROM python:3.12-slim

# ffmpeg lets yt-dlp merge and convert media.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

# Same uid/gid as the telegram-bot-api image, so handlers can read and remove files
# the Bot API server downloaded into the shared data directory.
RUN groupadd -g 101 app && useradd -u 101 -g app -M -d /app -s /usr/sbin/nologin app \
 && mkdir -p /files /packages /var/lib/telegram-bot-api && chown app:app /files /packages

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
# Extra packages installed from the orchestrator survive image updates here.
VOLUME ["/packages"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "--factory", "bot_runner.server:app", "--host", "0.0.0.0", "--port", "8000"]
