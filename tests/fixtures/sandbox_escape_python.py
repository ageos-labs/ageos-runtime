from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

workspace = Path(os.environ["AGEOS_WORKSPACE"])
home = Path(os.environ["HOME"])
tmpdir = Path(os.environ["TMPDIR"])
host_canary = Path(os.environ["HOST_CANARY"])
expect_network_blocked = os.environ["EXPECT_NETWORK_BLOCKED"] == "1"
category = sys.argv[1] if len(sys.argv) > 1 else "all"

failures: list[str] = []


def fail_if_succeeds(name: str, action) -> None:
    try:
        action()
    except Exception:
        return
    failures.append(name)


def fail_if_command_succeeds(name: str, command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode == 0:
        failures.append(name)


# Sanity: normal in-sandbox writes must still work before the adversarial phase.
(workspace / "inside-python-workspace.txt").write_text("workspace-ok", encoding="utf-8")
(home / "inside-python-home.txt").write_text("home-ok", encoding="utf-8")
(tmpdir / "inside-python-tmp.txt").write_text("tmp-ok", encoding="utf-8")

for key in list(os.environ):
    if key.startswith("AGEOS_") or key in {
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    }:
        os.environ.pop(key, None)

if any(key.startswith("AGEOS_") for key in os.environ):
    failures.append("AGEOS env vars remained available during Python escape attempts")


def run_fs_direct() -> None:
    fail_if_succeeds("read host canary outside workspace", lambda: host_canary.read_text(encoding="utf-8"))
    fail_if_succeeds("overwrite host canary outside workspace", lambda: host_canary.write_text("escaped", encoding="utf-8"))
    fail_if_succeeds("append host canary outside workspace", lambda: host_canary.open("a", encoding="utf-8").write("escaped"))
    fail_if_succeeds("unlink host canary outside workspace", lambda: host_canary.unlink())
    fail_if_succeeds(
        "create sibling outside workspace",
        lambda: (host_canary.parent / "created-by-python-sandbox").write_text("escaped", encoding="utf-8"),
    )


def run_fs_links() -> None:
    workspace_symlink = workspace / "canary-link"
    fail_if_succeeds(
        "write host canary through workspace symlink",
        lambda: (
            workspace_symlink.unlink(missing_ok=True),
            workspace_symlink.symlink_to(host_canary),
            workspace_symlink.write_text("escaped", encoding="utf-8"),
        ),
    )
    fail_if_succeeds("hardlink host canary into workspace", lambda: os.link(host_canary, workspace / "canary-hardlink"))
    rename_source = workspace / "rename-source-python"
    rename_source.write_text("rename-source", encoding="utf-8")
    fail_if_succeeds("rename workspace file over host canary", lambda: os.rename(rename_source, host_canary))


def run_protected_paths() -> None:
    fail_if_succeeds("write /etc/passwd", lambda: Path("/etc/passwd").open("a", encoding="utf-8").write("escaped"))
    fail_if_succeeds("write /usr/local/bin/ageos", lambda: Path("/usr/local/bin/ageos").open("a", encoding="utf-8").write("escaped"))
    fail_if_succeeds("write /opt/ageos", lambda: Path("/opt/ageos/.ageos-escape").write_text("escaped", encoding="utf-8"))
    fail_if_succeeds("write host /tmp", lambda: Path("/tmp/ageos-sandbox-escape-python").write_text("escaped", encoding="utf-8"))
    fail_if_succeeds("write proc sysctl", lambda: Path("/proc/sys/kernel/hostname").open("w", encoding="utf-8").write("escaped\n"))


def run_namespace_tools() -> None:
    fail_if_command_succeeds("nsenter host mount namespace", ["sh", "-c", "command -v nsenter >/dev/null 2>&1 && nsenter -t 1 -m true"])


def run_network_isolated() -> None:
    if not expect_network_blocked:
        return

    def connect_public_network() -> None:
        with socket.create_connection(("1.1.1.1", 80), timeout=1):
            pass

    fail_if_succeeds("public network connect while network is isolated", connect_public_network)


categories = {
    "env": lambda: None,
    "fs-direct": run_fs_direct,
    "fs-links": run_fs_links,
    "protected-paths": run_protected_paths,
    "namespace-tools": run_namespace_tools,
    "network-isolated": run_network_isolated,
}

if category == "all":
    for run_category in categories.values():
        run_category()
elif category in categories:
    categories[category]()
else:
    print(f"unknown Python escape category: {category}", file=sys.stderr)
    raise SystemExit(2)

if failures:
    print(f"Python sandbox escape attempts unexpectedly succeeded ({category}):", file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    raise SystemExit(1)
