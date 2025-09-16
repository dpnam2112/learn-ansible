# ---- Builder with uv ----
FROM python:3.12-slim AS builder
WORKDIR /app

# system deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev pkg-config curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# copy project metadata first for caching
COPY pyproject.toml ./
# if you have a uv.lock, copy it too for reproducible builds
# COPY uv.lock ./

# create venv and install deps
RUN uv venv --seed --python=python3 && uv sync --frozen || uv sync

# now copy app code (keeps deps layer cached if code changes)
COPY . .

# re-sync in case code declared extras or optional deps
RUN uv sync --frozen || uv sync

# ---- Runtime ----
FROM python:3.12-slim
WORKDIR /app

# minimal runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
  && rm -rf /var/lib/apt/lists/*

# copy everything from builder (app + .venv)
COPY --from=builder /app /app

EXPOSE 8000
ENV APP_PORT=8000
CMD ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

