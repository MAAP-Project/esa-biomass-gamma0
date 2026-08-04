#!/usr/bin/env bash
set -euo pipefail

basedir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
root="$(cd "$basedir/../.." && pwd -P)"
UV_PROJECT="$root" uv sync --frozen --no-dev
