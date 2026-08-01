#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

basedir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
UV_PROJECT="$basedir" uv sync --frozen --no-dev
