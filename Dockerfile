FROM node:24-bookworm-slim AS frontend

WORKDIR /web
RUN corepack enable && corepack prepare pnpm@11.6.0 --activate
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/index.html web/tsconfig*.json web/vite.config.ts ./
COPY web/src ./src
RUN pnpm run build

FROM python:3.12-slim-bookworm AS app

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    HABITAT_DATA_DIR=/app/data

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
COPY contracts ./contracts
COPY migrations ./migrations
RUN uv sync --frozen --no-dev \
    && groupadd --gid 10001 habitat \
    && useradd --uid 10001 --gid habitat --create-home habitat \
    && mkdir /app/data \
    && chown habitat:habitat /app/data
COPY --from=frontend /web/dist ./web/dist

USER habitat
EXPOSE 8000
CMD ["habitat-web", "--host", "0.0.0.0", "--port", "8000"]
