FROM ghcr.io/astral-sh/uv:0.9.17-python3.13-bookworm-slim@sha256:4f0bb0bed02fc05d6361b4ed2c37cddd40ce6478abb222a2f30036e0888f6db5

ARG INCLUDE_FETCH=false
WORKDIR /app/esa-biomass-gamma0

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY dps/ dps/

RUN if [ "$INCLUDE_FETCH" = "true" ]; then \
        uv sync --frozen --no-dev --extra fetch; \
    else \
        uv sync --frozen --no-dev; \
    fi
