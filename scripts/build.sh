#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_PREFIX="${AGEOS_PREFIX:-/opt/ageos}"
BIN_DIR="${AGEOS_BIN_DIR:-/usr/local/bin}"
BUILD_DIR="$ROOT/libageos/build"
C_SOURCE_DIR="$ROOT/libageos"
SUDO="${SUDO:-sudo}"
AGEOS_GPU_MODE="${AGEOS_GPU:-auto}"
NATIVE_STAGE=""
PY_WHEEL_DIR=""

cleanup() {
  if [[ -n "$NATIVE_STAGE" ]]; then
    rm -rf "$NATIVE_STAGE"
  fi
  if [[ -n "$PY_WHEEL_DIR" ]]; then
    rm -rf "$PY_WHEEL_DIR"
  fi
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "AgeOS system-wide source install is Linux-only." >&2
  echo "Use the packaged CLI on Linux or set up a development venv manually on this platform." >&2
  exit 1
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  SUDO=""
fi

if ! command -v meson >/dev/null 2>&1; then
  echo "meson not found. Run ./scripts/install-deps.sh first." >&2
  exit 1
fi

echo "Building native AgeOS core..."
if [[ -f "$BUILD_DIR/meson-private/coredata.dat" ]]; then
  meson setup "$BUILD_DIR" "$C_SOURCE_DIR" --wipe --prefix=/usr/local
else
  rm -rf "$BUILD_DIR"
  meson setup "$BUILD_DIR" "$C_SOURCE_DIR" --prefix=/usr/local
fi
meson compile -C "$BUILD_DIR"
NATIVE_STAGE="$(mktemp -d)"
meson install -C "$BUILD_DIR" --no-rebuild --destdir "$NATIVE_STAGE"
${SUDO} mkdir -p /usr/local
${SUDO} cp -a --remove-destination "$NATIVE_STAGE/usr/local/." /usr/local/
if command -v ldconfig >/dev/null 2>&1; then
  ${SUDO} ldconfig
fi

echo "Building AgeOS Python wheel..."
PY_WHEEL_DIR="$(mktemp -d)"
"$PYTHON_BIN" -m pip install --upgrade pip build
"$PYTHON_BIN" -m build --wheel --outdir "$PY_WHEEL_DIR" "$ROOT"
shopt -s nullglob
AGEOS_WHEELS=("$PY_WHEEL_DIR"/ageos-*.whl)
shopt -u nullglob
if [[ ${#AGEOS_WHEELS[@]} -eq 0 ]]; then
  echo "Failed to build AgeOS wheel." >&2
  exit 1
fi

echo "Installing AgeOS Python runtime into ${INSTALL_PREFIX}..."
${SUDO} rm -rf "$INSTALL_PREFIX"
${SUDO} mkdir -p "$INSTALL_PREFIX"
${SUDO} "$PYTHON_BIN" -m venv "$INSTALL_PREFIX"
${SUDO} "$INSTALL_PREFIX/bin/python" -m pip install --upgrade pip
${SUDO} "$INSTALL_PREFIX/bin/python" -m pip install --find-links "$PY_WHEEL_DIR" "${AGEOS_WHEELS[0]}"
${SUDO} env AGEOS_GPU="$AGEOS_GPU_MODE" "$INSTALL_PREFIX/bin/python" -m ageos.gpu_setup \
  --mode "$AGEOS_GPU_MODE" \
  --wheel "${AGEOS_WHEELS[0]}" \
  --profile-out "$INSTALL_PREFIX/install-profile.json"
${SUDO} mv "$INSTALL_PREFIX/bin/ageos" "$INSTALL_PREFIX/bin/ageos-entrypoint"
${SUDO} mv "$INSTALL_PREFIX/bin/ageos-node" "$INSTALL_PREFIX/bin/ageos-node-entrypoint"

echo "Linking global AgeOS commands into ${BIN_DIR}..."
${SUDO} mkdir -p "$BIN_DIR"
${SUDO} rm -f "$BIN_DIR/ageos" "$BIN_DIR/ageos-node"
${SUDO} tee "$BIN_DIR/ageos" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$INSTALL_PREFIX/bin/python" -I -c 'import sys; sys.argv[0] = "ageos"; from ageos.cli.main import run_cli; run_cli()' "\$@"
EOF
${SUDO} chmod 0755 "$BIN_DIR/ageos"
${SUDO} tee "$BIN_DIR/ageos-node" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$INSTALL_PREFIX/bin/python" -I -c 'import sys; sys.argv[0] = "ageos-node"; from ageos.node.daemon import main; raise SystemExit(main())' "\$@"
EOF
${SUDO} chmod 0755 "$BIN_DIR/ageos-node"
${SUDO} ln -sf "$INSTALL_PREFIX/bin/pytest" "$BIN_DIR/pytest"
if [[ -x /usr/local/bin/ageos-sandbox && "$BIN_DIR/ageos-sandbox" != "/usr/local/bin/ageos-sandbox" ]]; then
  ${SUDO} ln -sf /usr/local/bin/ageos-sandbox "$BIN_DIR/ageos-sandbox"
fi

echo
echo "AgeOS system install is ready."
echo "Run: ageos --help"
