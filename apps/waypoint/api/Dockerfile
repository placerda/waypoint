FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.23 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY corpus ./corpus
ENV APP_REQUIRE_POSTGRES=true \
    APP_DATABASE_POOL_MIN_SIZE=1 \
    APP_DATABASE_POOL_MAX_SIZE=4 \
    APP_MSAL_BEARER_VALIDATION_ENABLED=true \
    APP_LOCAL_AUTH_ENABLED=false \
    APP_API_KEY_AUTH_ENABLED=false \
    APP_DEFAULT_SEED_ENABLED=false \
    APP_SEED_REQUIRED=true \
    APP_RUN_STARTUP_DATABASE_BOOTSTRAP=false \
    APP_LEDGERFIELD_SEED_PATH=/app/corpus/data/waypoint/waypoint-seed.json \
    APP_CORPUS_LOCAL_ROOT=/app/corpus
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
