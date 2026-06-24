#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_COVERAGE_THRESHOLD="${PYTHON_COVERAGE_THRESHOLD:-85}"
C_COVERAGE_THRESHOLD="${C_COVERAGE_THRESHOLD:-85}"

find libageos/build -name '*.gcda' -delete
meson test -C libageos/build --print-errorlogs
gcovr \
  --root "$ROOT" \
  --filter 'libageos/(hw_detect|log)\.c$' \
  --exclude-directories 'libageos/build' \
  --print-summary \
  --txt \
  --fail-under-line "$C_COVERAGE_THRESHOLD"

find libageos/build -name '*.gcda' -delete
pytest \
  --cov=ageos.engine \
  --cov=ageos.http_api \
  --cov=ageos.inference \
  --cov=ageos.integrations \
  --cov=ageos.log \
  --cov=ageos.node.client \
  --cov-report=term \
  --cov-fail-under="$PYTHON_COVERAGE_THRESHOLD" \
  -m "not integration" \
  "$@"
