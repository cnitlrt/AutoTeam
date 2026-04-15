FROM python:3.12-slim AS frontend

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*
ENV BUN_INSTALL=/usr/local/bun
ENV PATH="/usr/local/bun/bin:${PATH}"
RUN curl -fsSL https://bun.sh/install | bash

WORKDIR /build/web
COPY web/package.json web/bun.lock* ./
RUN bun install --frozen-lockfile || bun install
COPY web/ ./
# next.config.mjs emits to ../src/autoteam/web/dist — create target
RUN mkdir -p /build/src/autoteam/web
RUN bun run build


FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    curl \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

RUN uv run playwright install chromium && uv run playwright install-deps chromium

COPY src/ src/
# Copy the Next.js static export built in the frontend stage
COPY --from=frontend /build/src/autoteam/web/dist /app/src/autoteam/web/dist

VOLUME ["/app/data"]

RUN mkdir -p /app/data
ENV DISPLAY=:99

EXPOSE 8787

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["api"]
