from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import typer

from ageos.inference import apply_inference_env
from ageos.node.client import SchedulerClient


def command(
    ctx: typer.Context,
    binary: str = typer.Option(..., "--binary", help="Agent binary path or command name."),
    niceness: int = typer.Option(0, "--niceness", min=-20, max=19, help="AgeOS GPU/memory priority."),
    memory: str = typer.Option("2G", "--memory", help="Sandbox memory limit."),
    cpu: int = typer.Option(0, "--cpu", help="Optional cgroup CPU percent cap."),
    speciality: str | None = typer.Option(None, "--speciality", help="Default model specialty for this agent."),
    workdir: Path | None = typer.Option(None, "--workdir", file_okay=False, dir_okay=True),
    root_dir: Path | None = typer.Option(
        None,
        "--root-dir",
        file_okay=False,
        dir_okay=True,
        help="Writable directory exposed inside the sandbox. Defaults to an empty /workspace.",
    ),
    unsafe_no_sandbox: bool = typer.Option(False, "--unsafe-no-sandbox", help="Development fallback only."),
) -> None:
    """Run a binary as an AgeOS agent inside the hardened sandbox."""

    run_agent(
        binary=binary,
        extra_args=list(ctx.args),
        niceness=niceness,
        memory=memory,
        cpu=cpu,
        speciality=speciality,
        workdir=workdir,
        root_dir=root_dir,
        unsafe_no_sandbox=unsafe_no_sandbox,
    )


def run_agent(
    *,
    binary: str,
    extra_args: list[str],
    niceness: int,
    memory: str,
    cpu: int,
    speciality: str | None,
    workdir: Path | None,
    root_dir: Path | None = None,
    unsafe_no_sandbox: bool = False,
) -> None:
    """Run a binary as an AgeOS agent inside the hardened sandbox."""

    client = SchedulerClient.local()
    sandbox_paths = _resolve_sandbox_paths(root_dir, workdir)
    cwd_path = sandbox_paths.host_workdir
    resolved_binary = _resolve_binary(binary, cwd_path)
    agent_id = client.register_agent(str(resolved_binary), niceness=niceness, specialty=speciality)
    env = os.environ.copy()
    env["AGEOS_AGENT_ID"] = agent_id
    env["AGEOS_NICENESS"] = str(niceness)
    env.setdefault("AGEOS_CACHE", str(Path.home() / ".cache" / "ageos"))
    _add_pnpm_home_to_path(env)
    endpoint = apply_inference_env(env, speciality)
    typer.echo(f"Using AgeOS inference endpoint at {endpoint}")
    cwd = sandbox_paths.sandbox_workdir
    target_args = [*_argv_for_binary(resolved_binary), *extra_args]
    try:
        if platform.system() != "Linux" and not unsafe_no_sandbox:
            raise typer.BadParameter("ageos run sandbox is Linux-only; use --unsafe-no-sandbox for local development")
        if unsafe_no_sandbox:
            raise typer.Exit(subprocess.call(target_args, cwd=cwd, env=env))
        inference = _sandbox_inference_endpoint(endpoint)
        _apply_sandbox_inference_env(env, inference)
        exit_code = _run_native_sandbox(
            client,
            target_args,
            memory=memory,
            cpu=cpu,
            niceness=niceness,
            workdir=cwd,
            root_dir=sandbox_paths.host_root_dir,
            env=env,
            isolate_network=True,
            inference_host=inference.host,
            inference_port=inference.host_port,
            sandbox_inference_port=inference.sandbox_port,
        )
        raise typer.Exit(exit_code)
    finally:
        client.deregister_agent(agent_id)


def _resolve_binary(binary: str, cwd: Path) -> Path:
    candidate = Path(binary).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        paths = [candidate] if candidate.is_absolute() else [Path.cwd() / candidate, cwd / candidate]
        for path in paths:
            resolved = path.resolve()
            if resolved.exists() and resolved.is_file():
                return resolved
        raise typer.BadParameter(f"binary not found: {binary}")

    local_bin = cwd / "node_modules" / ".bin" / binary
    if local_bin.exists() and local_bin.is_file():
        return local_bin.resolve()

    found = shutil.which(binary)
    if found:
        return Path(found).resolve()

    raise typer.BadParameter(f"binary not found on PATH or node_modules/.bin: {binary}")


def _argv_for_binary(binary: Path) -> list[str]:
    if binary.suffix == ".py":
        return [_ageos_python(), str(binary)]
    return [str(binary)]


def _ageos_python() -> str:
    ageos_python = Path(os.environ.get("AGEOS_PYTHON", "/opt/ageos/bin/python"))
    return str(ageos_python if ageos_python.exists() else Path(sys.executable))


def _add_pnpm_home_to_path(env: dict[str, str]) -> None:
    pnpm_home = env.get("PNPM_HOME") or str(Path.home() / ".local" / "share" / "pnpm")
    env["PNPM_HOME"] = pnpm_home
    path_parts = [str(Path(pnpm_home) / "bin"), pnpm_home, env.get("PATH", "")]
    env["PATH"] = ":".join(part for part in path_parts if part)


def _run_native_sandbox(
    client: SchedulerClient,
    target_args: list[str],
    *,
    memory: str,
    cpu: int,
    niceness: int,
    workdir: str,
    root_dir: str | None,
    env: dict[str, str],
    isolate_network: bool,
    inference_host: str | None = None,
    inference_port: int = 0,
    sandbox_inference_port: int = 0,
) -> int:
    if not target_args:
        raise typer.BadParameter("missing sandbox command")
    original_env = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        return client.native.run_sandbox(
            target_args[0],
            target_args,
            resource_niceness=niceness,
            memory_max=_parse_bytes(memory),
            cpu_percent=cpu,
            workdir=workdir,
            root_dir=root_dir,
            isolate_network=isolate_network,
            inference_host=inference_host,
            inference_port=inference_port,
            sandbox_inference_port=sandbox_inference_port,
        )
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def _parse_bytes(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        raise typer.BadParameter("memory limit cannot be empty")
    suffix = stripped[-1].lower()
    number = stripped[:-1] if suffix in {"g", "m"} else stripped
    try:
        base = int(number)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid memory limit: {value}") from exc
    if suffix == "g":
        return base * 1024 * 1024 * 1024
    if suffix == "m":
        return base * 1024 * 1024
    return base


class SandboxPaths:
    def __init__(self, host_workdir: Path, sandbox_workdir: str, host_root_dir: str | None) -> None:
        self.host_workdir = host_workdir
        self.sandbox_workdir = sandbox_workdir
        self.host_root_dir = host_root_dir


def _resolve_sandbox_paths(root_dir: Path | None, workdir: Path | None) -> SandboxPaths:
    if root_dir is None:
        return SandboxPaths(host_workdir=workdir or Path.cwd(), sandbox_workdir="/workspace", host_root_dir=None)
    resolved_root = root_dir.expanduser().resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise typer.BadParameter(f"root directory not found: {root_dir}")
    _validate_writable_root(resolved_root)
    resolved_workdir = (workdir.expanduser().resolve() if workdir is not None else resolved_root)
    if not _is_relative_to(resolved_workdir, resolved_root):
        raise typer.BadParameter("--workdir must be inside --root-dir")
    return SandboxPaths(
        host_workdir=resolved_workdir,
        sandbox_workdir=str(resolved_workdir),
        host_root_dir=str(resolved_root),
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_writable_root(root: Path) -> None:
    protected_roots = [
        Path("/"),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/lib"),
        Path("/lib64"),
        Path("/opt"),
        Path("/etc"),
        Path("/var"),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
        Path("/run"),
    ]
    for protected in protected_roots:
        if root == protected or (protected != Path("/") and _is_relative_to(root, protected)):
            raise typer.BadParameter(f"--root-dir cannot be inside protected system path: {protected}")
    source_root = _source_checkout_root()
    if source_root is not None and (
        root == source_root
        or _is_relative_to(source_root, root)
        or (_is_relative_to(root, source_root) and not _is_allowed_source_workspace(root, source_root))
    ):
        raise typer.BadParameter("--root-dir cannot include the AgeOS application source tree")
    source_tree = _ageos_source_tree_for(root)
    if source_tree is not None and not _is_allowed_source_workspace(root, source_tree):
        raise typer.BadParameter("--root-dir cannot be inside the AgeOS application source tree")


def _source_checkout_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").exists() and (candidate / "ageos").is_dir():
        return candidate
    return None


def _ageos_source_tree_for(root: Path) -> Path | None:
    for candidate in (root, *root.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "ageos" / "cli" / "run.py").exists()
            and (candidate / "libageos" / "sandbox.c").exists()
        ):
            return candidate
    return None


def _is_allowed_source_workspace(root: Path, source_root: Path) -> bool:
    examples = source_root / "examples"
    return root == examples or _is_relative_to(root, examples)


def _apply_sandbox_inference_env(env: dict[str, str], endpoint: SandboxInferenceEndpoint) -> None:
    env["AGEOS_API_BASE_URL"] = endpoint.sandbox_base_url
    env["OPENAI_BASE_URL"] = f"{endpoint.sandbox_base_url}/v1"
    env["AGEOS_SANDBOX_INFERENCE_HOST"] = "127.0.0.1"
    env["AGEOS_SANDBOX_INFERENCE_PORT"] = str(endpoint.sandbox_port)
    env["AGEOS_NETWORK"] = "inference-only"


class SandboxInferenceEndpoint:
    def __init__(self, host: str, host_port: int, sandbox_port: int) -> None:
        self.host = host
        self.host_port = host_port
        self.sandbox_port = sandbox_port
        self.sandbox_base_url = f"http://127.0.0.1:{sandbox_port}"


def _sandbox_inference_endpoint(host_base_url: str) -> SandboxInferenceEndpoint:
    parsed = urlparse(host_base_url)
    if parsed.scheme not in {"http", ""}:
        raise typer.BadParameter("sandboxed inference endpoint must use HTTP")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    return SandboxInferenceEndpoint(host=host, host_port=port, sandbox_port=port)
