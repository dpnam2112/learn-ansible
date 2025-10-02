# Use Debian 12 (bookworm) so Percona repos are supported
FROM python:3.12-slim-bookworm AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

# Base OS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release \
 && rm -rf /var/lib/apt/lists/*

# Percona repo -> mysqlbinlog + mysql client
RUN set -eux; \
    curl -fsSL https://repo.percona.com/apt/percona-release_latest.generic_all.deb -o /tmp/percona-release.deb; \
    apt-get update; \
    apt-get install -y /tmp/percona-release.deb; \
    # ps80 works on bookworm; -y avoids any prompts
    percona-release setup -y ps80; \
    apt-get update; \
    apt-get install -y --no-install-recommends percona-server-client; \
    rm -rf /var/lib/apt/lists/* /tmp/percona-release.deb

# optional: uv
RUN pip install uv

WORKDIR /app
COPY cli /app
COPY pyproject.toml /app
COPY uv.lock /app

# install project in editable mode
RUN uv pip install -e . --system

CMD ["sleep", "infinity"]
