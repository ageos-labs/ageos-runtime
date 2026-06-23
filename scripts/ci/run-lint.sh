#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "--- Python lint (ruff) ---"
ruff check .
ruff format --check .

echo "--- C lint (clang-format) ---"
find libageos -name '*.c' -o -name '*.h' | xargs clang-format --dry-run --Werror

echo "All lint checks passed."
