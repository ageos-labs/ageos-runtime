<div align="center">
  <img src="assets/ageos-logo.png" alt="AgeOS logo" width="120">
  <h1>AgeOS</h1>
  <p>Local LLM serving and sandboxed agents in one command.</p>
  <p>
    <a href="https://github.com/ageos-labs/ageos-runtime/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ageos-labs/ageos-runtime/actions/workflows/ci.yml/badge.svg"></a>
    <a href="https://github.com/ageos-labs/ageos-runtime/releases/latest"><img alt="GitHub release" src="https://img.shields.io/github/v/release/ageos-labs/ageos-runtime?display_name=tag"></a>
    <a href="https://discord.gg/skwKqSgvD2"><img alt="Discord" src="https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white"></a>
    <a href="LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/license-Apache%202.0-blue.svg"></a>
  </p>
</div>

## Install

Linux:

```bash
curl -fsSL https://github.com/ageos-labs/ageos-runtime/releases/latest/download/install.sh | bash
```

Windows PowerShell, through WSL:

```powershell
irm https://github.com/ageos-labs/ageos-runtime/releases/latest/download/install.ps1 | iex
```

The installer downloads the latest GitHub Release artifact, installs local runtime dependencies, builds AgeOS, and links `ageos` into `/usr/local/bin`.

Check it:

```bash
ageos --help
```

Docker image:

```bash
docker pull ghcr.io/ageos-labs/ageos-runtime:latest
```

Use a release image as a base:

```dockerfile
FROM ghcr.io/ageos-labs/ageos-runtime:v0.1.0
```

## Quick Start

Ask the local model a question:

```bash
ageos prompt --text "Say hello from AgeOS"
```

Start the OpenAI-compatible local endpoint:

```bash
ageos serve
```

Run an agent in the sandbox:

```bash
ageos run --root-dir . --binary ./examples/basic_agent.py --memory 16G
```

Pick or inspect models:

```bash
ageos models
ageos models list
ageos models stop
```

## What AgeOS Does

- Runs local LLMs.
- Exposes an OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1`.
- Keeps warm model backends shared across agents.
- Runs agents in a Linux sandbox with restricted filesystem and network access.
- Injects local inference into agents as `OPENAI_BASE_URL` and `OPENAI_API_KEY`.

## Agent Environment

`ageos run` starts the shared inference endpoint before launching an agent and injects:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_API_KEY=ageos-local
AGEOS_API_BASE_URL=http://127.0.0.1:8000
```

Sandboxed agents only get access to the local inference endpoint. General outbound network access is blocked.

## OpenClaw Example

```bash
cd examples/openclaw
ageos run --memory 16G --root-dir openclaw --binary openclaw/node_modules/.bin/openclaw tui
```

## Releases

AgeOS ships source install assets from GitHub Releases and runtime images from GHCR.

Push a `v*` tag. The release workflow runs unit tests, runs local-inference integration tests, then publishes:

- `install.sh`
- `install.ps1`
- `ageos-source.tar.gz`
- `container-image.txt`
- `SHA256SUMS`

Install a specific tag:

```bash
curl -fsSL https://github.com/ageos-labs/ageos-runtime/releases/latest/download/install.sh | AGEOS_VERSION=v0.1.0 bash
```

Use the matching runtime image:

```bash
docker pull ghcr.io/ageos-labs/ageos-runtime:v0.1.0
```

## Docker CI

CI builds Docker targets from `docker/Dockerfile` with GitHub Actions layer caching:

```bash
docker build -f docker/Dockerfile --target unit-test -t ageos-runtime:unit .
docker run --rm --privileged --security-opt seccomp=unconfined ageos-runtime:unit
```

Integration tests need Linux sandbox permissions and mounted caches:

```bash
mkdir -p .ageos-cache .pnpm-store
docker build -f docker/Dockerfile --target integration-test -t ageos-runtime:integration .
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v "$PWD/.ageos-cache:/cache/ageos" \
  -v "$PWD/.pnpm-store:/cache/pnpm-store" \
  ageos-runtime:integration
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
