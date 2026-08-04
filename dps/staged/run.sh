#!/usr/bin/env bash
set -euo pipefail

basedir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
root="$(cd "$basedir/../.." && pwd -P)"
mkdir -p output
UV_PROJECT="$root" uv run --frozen --no-dev "$basedir/run.py" "$@" --output-root output
