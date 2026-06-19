from __future__ import annotations

import ctypes
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HardwareInfo:
    ram_bytes: int
    vram_bytes: int


@dataclass(frozen=True)
class Admission:
    allowed: bool
    state: str
    reason: str = ""


class LibAgeosError(RuntimeError):
    pass


class SandboxConfig(ctypes.Structure):
    _fields_ = [
        ("binary", ctypes.c_char_p),
        ("argv", ctypes.POINTER(ctypes.c_char_p)),
        ("resource_niceness", ctypes.c_int),
        ("memory_max", ctypes.c_uint64),
        ("cpu_percent", ctypes.c_uint32),
        ("workdir", ctypes.c_char_p),
        ("root_dir", ctypes.c_char_p),
        ("isolate_network", ctypes.c_int),
        ("inference_host", ctypes.c_char_p),
        ("inference_port", ctypes.c_uint32),
        ("sandbox_inference_port", ctypes.c_uint32),
    ]


AGEOS_AGENT_UID_BASE = 60000
AGEOS_AGENT_UID_END = 64000


def _load_libageos() -> ctypes.CDLL:
    candidates = [
        Path(__file__).resolve().parent / "libageos.so",
        Path(__file__).resolve().parent.parent / "libageos" / "build" / "libageos.so",
        Path("/usr/lib/libageos.so"),
        Path("/usr/lib/x86_64-linux-gnu/libageos.so"),
        Path("/usr/local/lib/libageos.so"),
        Path("/usr/local/lib/x86_64-linux-gnu/libageos.so"),
    ]
    errors: list[str] = []
    for path in candidates:
        if path.exists():
            try:
                return ctypes.CDLL(str(path))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    detail = "; ".join(errors) if errors else "no candidate library path exists"
    raise LibAgeosError(
        "libageos.so is required but could not be loaded. "
        "Run ./scripts/build.sh or install the AgeOS native package. "
        f"Details: {detail}"
    )


def _bytes(value: str | None) -> bytes | None:
    if value is None:
        return None
    return value.encode("utf-8")


def detect_hardware() -> HardwareInfo:
    """Return host RAM/VRAM from the required native AgeOS library."""

    lib = _load_libageos()
    try:
        lib.ageos_hw_total_ram_bytes.restype = ctypes.c_uint64
        lib.ageos_hw_vram_bytes.restype = ctypes.c_uint64
        return HardwareInfo(
            ram_bytes=int(lib.ageos_hw_total_ram_bytes()),
            vram_bytes=int(lib.ageos_hw_vram_bytes()),
        )
    except AttributeError as exc:
        raise LibAgeosError("libageos.so is missing required hardware detection symbols") from exc


def is_sandboxed() -> bool:
    """Return true when kernel namespace state shows this process is inside AgeOS sandbox."""

    if _has_sandbox_agent_uid():
        return True
    if _has_sandbox_user_namespace():
        return True
    return os.environ.get("AGEOS_SANDBOX") == "1"


def _has_sandbox_agent_uid() -> bool:
    return AGEOS_AGENT_UID_BASE <= os.geteuid() < AGEOS_AGENT_UID_END


def _has_sandbox_user_namespace() -> bool:
    try:
        text = Path("/proc/self/uid_map").read_text(encoding="utf-8")
    except PermissionError:
        return os.geteuid() == 0
    except OSError:
        return False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            inside_uid, outside_uid, count = (int(part) for part in parts)
        except ValueError:
            continue
        if outside_uid != inside_uid and count == 1:
            return True
    return False


class NativeScheduler:
    def __init__(self, lib: ctypes.CDLL | None = None) -> None:
        self.lib = lib if lib is not None else _load_libageos()
        self._configure()

    def admit_model_job(
        self,
        specialty: str,
        model_name: str,
        niceness: int,
        ram_gb: float,
        vram_gb: float,
    ) -> Admission:
        allowed = ctypes.c_int()
        state = ctypes.create_string_buffer(64)
        reason = ctypes.create_string_buffer(256)
        result = self.lib.ageos_scheduler_admit_model_job(
            _bytes(specialty),
            _bytes(model_name),
            int(niceness),
            float(ram_gb),
            float(vram_gb),
            ctypes.byref(allowed),
            state,
            ctypes.sizeof(state),
            reason,
            ctypes.sizeof(reason),
        )
        if int(result) != 0:
            raise LibAgeosError("native scheduler admission failed")
        return Admission(
            allowed=bool(allowed.value),
            state=state.value.decode("utf-8"),
            reason=reason.value.decode("utf-8"),
        )

    def configure_limits(self, ram_limit_gb: float | None, vram_limit_gb: float | None) -> None:
        result = self.lib.ageos_scheduler_configure_limits(
            float(ram_limit_gb or 0),
            float(vram_limit_gb or 0),
        )
        if int(result) != 0:
            raise LibAgeosError("native scheduler failed to configure resource limits")

    def register_agent(
        self,
        agent_id: str,
        pid: int,
        binary: str,
        niceness: int,
        specialty: str | None,
    ) -> None:
        result = self.lib.ageos_scheduler_register_agent(  # type: ignore[union-attr]
            _bytes(agent_id),
            int(pid),
            _bytes(binary),
            int(niceness),
            _bytes(specialty),
        )
        if int(result) != 0:
            raise LibAgeosError("native scheduler failed to register agent")

    def deregister_agent(self, agent_id: str) -> None:
        result = self.lib.ageos_scheduler_deregister_agent(_bytes(agent_id))  # type: ignore[union-attr]
        if int(result) != 0:
            raise LibAgeosError("native scheduler failed to deregister agent")

    def mark_model_loaded(
        self,
        name: str,
        specialty: str,
        backend: str,
        ram_gb: float,
        vram_gb: float,
        pid: int,
        port: int,
    ) -> None:
        result = self.lib.ageos_scheduler_mark_model_loaded(  # type: ignore[union-attr]
            _bytes(name),
            _bytes(specialty),
            _bytes(backend),
            float(ram_gb),
            float(vram_gb),
            int(pid),
            int(port),
        )
        if int(result) != 0:
            raise LibAgeosError("native scheduler failed to mark model loaded")

    def mark_model_unloaded(self, name: str) -> None:
        result = self.lib.ageos_scheduler_mark_model_unloaded(_bytes(name))  # type: ignore[union-attr]
        if int(result) != 0:
            raise LibAgeosError("native scheduler failed to mark model unloaded")

    def evict_model(self, name: str) -> None:
        result = self.lib.ageos_scheduler_evict_model(_bytes(name))  # type: ignore[union-attr]
        if int(result) != 0:
            raise LibAgeosError("native scheduler failed to evict model")

    def add_queue_item(
        self,
        job_id: str,
        kind: str,
        specialty: str,
        model_name: str,
        niceness: int,
        reason: str,
    ) -> None:
        result = self.lib.ageos_scheduler_add_queue_item(  # type: ignore[union-attr]
            _bytes(job_id),
            _bytes(kind),
            _bytes(specialty),
            _bytes(model_name),
            int(niceness),
            _bytes(reason),
        )
        if int(result) != 0:
            raise LibAgeosError("native scheduler failed to add queue item")

    def snapshot(self) -> dict[str, object]:
        pointer = self.lib.ageos_scheduler_snapshot_json()  # type: ignore[union-attr]
        if not pointer:
            raise LibAgeosError("native scheduler failed to build snapshot")
        try:
            raw = ctypes.string_at(pointer).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LibAgeosError("native scheduler returned a non-object snapshot")
            return data
        finally:
            self.lib.ageos_scheduler_free_string(pointer)  # type: ignore[union-attr]

    def run_sandbox(
        self,
        binary: str,
        argv: list[str],
        *,
        resource_niceness: int,
        memory_max: int,
        cpu_percent: int,
        workdir: str,
        isolate_network: bool,
        root_dir: str | None = None,
        inference_host: str | None = None,
        inference_port: int = 0,
        sandbox_inference_port: int = 0,
    ) -> int:
        encoded_args = [_bytes(arg) for arg in argv]
        argv_array = (ctypes.c_char_p * (len(encoded_args) + 1))()
        for index, value in enumerate(encoded_args):
            argv_array[index] = value
        argv_array[len(encoded_args)] = None
        config = SandboxConfig(
            binary=_bytes(binary),
            argv=argv_array,
            resource_niceness=int(resource_niceness),
            memory_max=int(memory_max),
            cpu_percent=int(cpu_percent),
            workdir=_bytes(workdir),
            root_dir=_bytes(root_dir),
            isolate_network=1 if isolate_network else 0,
            inference_host=_bytes(inference_host),
            inference_port=int(inference_port),
            sandbox_inference_port=int(sandbox_inference_port),
        )
        return int(self.lib.ageos_sandbox_run(ctypes.byref(config)))

    def _configure(self) -> None:
        assert self.lib is not None
        try:
            self.lib.ageos_scheduler_admit_model_job.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_char_p,
                ctypes.c_size_t,
                ctypes.c_char_p,
                ctypes.c_size_t,
            ]
            self.lib.ageos_scheduler_admit_model_job.restype = ctypes.c_int
            self.lib.ageos_scheduler_configure_limits.argtypes = [
                ctypes.c_double,
                ctypes.c_double,
            ]
            self.lib.ageos_scheduler_configure_limits.restype = ctypes.c_int
            self.lib.ageos_scheduler_register_agent.argtypes = [
                ctypes.c_char_p,
                ctypes.c_int64,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
            ]
            self.lib.ageos_scheduler_register_agent.restype = ctypes.c_int
            self.lib.ageos_scheduler_deregister_agent.argtypes = [ctypes.c_char_p]
            self.lib.ageos_scheduler_deregister_agent.restype = ctypes.c_int
            self.lib.ageos_scheduler_mark_model_loaded.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_int64,
                ctypes.c_int,
            ]
            self.lib.ageos_scheduler_mark_model_loaded.restype = ctypes.c_int
            self.lib.ageos_scheduler_mark_model_unloaded.argtypes = [ctypes.c_char_p]
            self.lib.ageos_scheduler_mark_model_unloaded.restype = ctypes.c_int
            self.lib.ageos_scheduler_evict_model.argtypes = [ctypes.c_char_p]
            self.lib.ageos_scheduler_evict_model.restype = ctypes.c_int
            self.lib.ageos_scheduler_add_queue_item.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
            ]
            self.lib.ageos_scheduler_add_queue_item.restype = ctypes.c_int
            self.lib.ageos_scheduler_snapshot_json.argtypes = []
            self.lib.ageos_scheduler_snapshot_json.restype = ctypes.c_void_p
            self.lib.ageos_scheduler_free_string.argtypes = [ctypes.c_void_p]
            self.lib.ageos_scheduler_free_string.restype = None
            self.lib.ageos_sandbox_run.argtypes = [ctypes.POINTER(SandboxConfig)]
            self.lib.ageos_sandbox_run.restype = ctypes.c_int
        except AttributeError as exc:
            raise LibAgeosError("libageos.so is missing required scheduler or sandbox symbols") from exc
