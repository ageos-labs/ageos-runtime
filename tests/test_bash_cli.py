from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _require_ageos_cli() -> None:
    if shutil.which("ageos") is None:
        pytest.skip("ageos is not installed")


def _cli_e2e_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("AGEOS_API_BASE_URL", "http://127.0.0.1:8000")
    env.setdefault("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    return env


def _run_cli_e2e(
    command: list[str],
    *,
    tmp_path: Path,
    stdin: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    _require_ageos_cli()
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_cli_e2e_env(tmp_path),
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    return result


@pytest.mark.parametrize(
    "command",
    [
        ["ageos", "shell", "--root-dir", "examples/basic"],
        ["ageos", "shell", "--root-dir", "examples/basic", "--allow-network"],
    ],
)
def test_ageos_shell_examples_basic_cli_e2e(command: list[str], tmp_path: Path) -> None:
    result = _run_cli_e2e(command, tmp_path=tmp_path, stdin="exit\n")

    assert "Entering AgeOS sandbox shell" in result.stdout
    assert "Using AgeOS inference endpoint at http://127.0.0.1:8000" in result.stdout


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["ageos", "--version"], "ageos "),
        (["ageos", "--help"], "AgeOS local agent runtime"),
        (["ageos", "specialties", "list"], "default-instruct"),
        (["ageos", "ps"], "Memory pressure:"),
        (["ageos", "queue"], "AgeOS Waiting Queue"),
    ],
)
def test_ageos_host_cli_tools_e2e(command: list[str], expected: str, tmp_path: Path) -> None:
    result = _run_cli_e2e(command, tmp_path=tmp_path)

    assert expected in result.stdout
