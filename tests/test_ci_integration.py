from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


def _integration_enabled() -> bool:
    return os.environ.get("AGEOS_RUN_INTEGRATION") == "1"


def _integration_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("AGEOS_API_BASE_URL", None)
    env.setdefault("AGEOS_CACHE", str(tmp_path / "ageos-cache"))
    env.setdefault("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    env.setdefault("AGEOS_LLAMA_CTX_SIZE", "512")
    env.setdefault("AGEOS_MAX_OUTPUT_TOKENS", "32")
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    return env


@pytest.fixture(scope="module")
def integration_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    _require_integration_runtime()
    env = _integration_env(tmp_path_factory.mktemp("ageos-integration"))
    _run(
        ["ageos", "prompt", "--text", "Reply with ok.", "--speciality", "default-instruct"],
        env=env,
        timeout=180,
    )
    return env


def _run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    return result


def _require_integration_runtime() -> None:
    if not _integration_enabled():
        pytest.skip("set AGEOS_RUN_INTEGRATION=1 to run real local-inference integration tests")
    for binary in ("ageos", "llama-server"):
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} is not installed")


def test_basic_agent_gets_model_response(integration_env: dict[str, str]) -> None:
    result = _run(
        [
            "ageos",
            "run",
            "--memory",
            "4G",
            "--root-dir",
            "examples",
            "--binary",
            "examples/basic_agent.py",
        ],
        env=integration_env,
        timeout=240,
    )

    assert "AgeOS basic agent starting" in result.stdout
    assert "model_response:" in result.stdout
    marker_index = result.stdout.index("model_response:")
    response = result.stdout[marker_index:].splitlines()[1].strip()
    assert response


def test_openclaw_onboard_configures_local_inference(integration_env: dict[str, str]) -> None:
    openclaw_root = ROOT / "examples" / "openclaw" / "openclaw"
    openclaw_binary = openclaw_root / "node_modules" / ".bin" / "openclaw"
    if not openclaw_binary.exists():
        pytest.skip("OpenClaw example dependencies are not installed")
    if shutil.which("node", path=integration_env.get("PATH")) is None:
        pytest.skip("node is not installed")

    state_root = openclaw_root / ".ageos"
    existing_configs = set(state_root.glob("agents/*/home/.openclaw/openclaw.json"))

    _run(
        [
            "ageos",
            "run",
            "--memory",
            "4G",
            "--root-dir",
            str(openclaw_root),
            "--binary",
            "openclaw",
            "onboard",
            "--non-interactive",
            "--accept-risk",
            "--mode",
            "local",
            "--auth-choice",
            "skip",
            "--custom-base-url",
            "http://127.0.0.1:8000/v1",
            "--custom-api-key",
            "ageos-local",
            "--custom-model-id",
            "default-instruct",
            "--custom-provider-id",
            "ageos-ci",
            "--custom-compatibility",
            "openai",
            "--skip-daemon",
            "--skip-channels",
            "--skip-skills",
            "--skip-search",
            "--skip-health",
            "--skip-ui",
            "--skip-hooks",
            "--json",
        ],
        env=integration_env,
        timeout=240,
    )

    configs = sorted(set(state_root.glob("agents/*/home/.openclaw/openclaw.json")) - existing_configs)
    assert configs, f"OpenClaw config was not created under {state_root}"
    config = json.loads(configs[-1].read_text(encoding="utf-8"))
    gateway = config.get("gateway", {})
    wizard = config.get("wizard", {})
    assert gateway.get("mode") == "local"
    assert gateway.get("auth", {}).get("mode") == "token"
    assert wizard.get("lastRunCommand") == "onboard"
    assert wizard.get("lastRunMode") == "local"
