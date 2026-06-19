#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_PARENT="$ROOT/examples/openclaw"
OPENCLAW_DIR="$OPENCLAW_PARENT/openclaw"
PNPM_STORE_DIR="${PNPM_STORE_DIR:-/cache/pnpm-store}"

mkdir -p "$OPENCLAW_PARENT" "$PNPM_STORE_DIR"

if [[ ! -d "$OPENCLAW_DIR/.git" ]]; then
  rm -rf "$OPENCLAW_DIR"
  git clone https://github.com/openclaw/openclaw.git --depth 1 "$OPENCLAW_DIR"
fi

cd "$OPENCLAW_DIR"

pnpm config set store-dir "$PNPM_STORE_DIR"
pnpm install --frozen-lockfile
