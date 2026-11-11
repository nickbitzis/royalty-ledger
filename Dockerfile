# Build stage: resolve and install dependencies with uv into a virtualenv.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime stage: slim image, non-root user, only the virtualenv.
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 royalty
WORKDIR /app
COPY --from=build --chown=royalty:royalty /app/.venv /app/.venv
# Synthetic reports, the catalog and review decisions are written under /app/data at runtime.
RUN mkdir -p /app/data && chown royalty:royalty /app/data
USER royalty
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 RL_DATA_DIR=/app/data
EXPOSE 8882
# First start generates the synthetic data and computes four quarters (about 30 seconds), hence the start period.
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=3 \
    CMD ["royaltyledger", "healthcheck", "--url", "http://127.0.0.1:8882/healthz"]
ENTRYPOINT ["royaltyledger", "--data", "/app/data"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8882"]
