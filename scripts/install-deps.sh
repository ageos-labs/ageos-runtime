#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "AgeOS runtime dependencies are Linux-focused. Install Python deps with pip on this platform."
  exit 0
fi

sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  curl \
  git \
  libseccomp-dev \
  meson \
  ninja-build \
  pkg-config \
  python3-dev \
  python3-full \
  python3-pip \
  python3-venv

LLAMA_CPP_REPO="${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp.git}"
LLAMA_CPP_REF="${LLAMA_CPP_REF:-master}"
LLAMA_CPP_SRC="${LLAMA_CPP_SRC:-/tmp/ageos-llama.cpp}"
LLAMA_CPP_BUILD="$LLAMA_CPP_SRC/build"
LLAMA_LIB_DIR="/usr/local/lib/x86_64-linux-gnu"

if ! command -v llama-server >/dev/null 2>&1; then
  echo "Installing llama.cpp server from ${LLAMA_CPP_REPO} (${LLAMA_CPP_REF})..."
  rm -rf "$LLAMA_CPP_SRC"
  git clone --depth 1 --branch "$LLAMA_CPP_REF" "$LLAMA_CPP_REPO" "$LLAMA_CPP_SRC"
  cmake -S "$LLAMA_CPP_SRC" -B "$LLAMA_CPP_BUILD" -DLLAMA_CURL=OFF
  cmake --build "$LLAMA_CPP_BUILD" --target llama-server --parallel "$(nproc)"
  sudo install -m 0755 "$LLAMA_CPP_BUILD/bin/llama-server" /usr/local/bin/llama-server
else
  echo "llama-server already available at $(command -v llama-server)"
fi
if [[ -d "$LLAMA_CPP_BUILD" ]]; then
  sudo mkdir -p "$LLAMA_LIB_DIR"
  find "$LLAMA_CPP_BUILD" -name 'lib*.so*' -exec sudo cp -a --remove-destination {} "$LLAMA_LIB_DIR"/ \;
  sudo ldconfig
fi

echo "Optional GPU path from repo root after build: sudo /opt/ageos/bin/python -m pip install '.[vllm]'"
