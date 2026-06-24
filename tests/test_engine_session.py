from __future__ import annotations

from pathlib import Path

import pytest

from ageos.engine.registry import ModelSpec
from ageos.engine.session import EngineSession, ResolvedSession
from ageos.native import HardwareInfo

GPU_MODEL = ModelSpec(
    name="gpu-model",
    flavor="qwen",
    capability="instruct",
    tier="small",
    backend="llama",
    repo_id="repo/gpu",
    filename="gpu.gguf",
    ram_gb=8,
    vram_gb=6,
    context_tokens=32768,
    placement="gpu",
)
CPU_MODEL = ModelSpec(
    name="cpu-model",
    flavor="mistral",
    capability="instruct",
    tier="small",
    backend="llama",
    repo_id="repo/cpu",
    filename="cpu.gguf",
    ram_gb=8,
    vram_gb=0,
    context_tokens=32768,
)
VLLM_MODEL = ModelSpec(
    name="vllm-model",
    flavor="qwen",
    capability="instruct",
    tier="large",
    backend="vllm",
    repo_id="repo/vllm",
    filename=None,
    ram_gb=16,
    vram_gb=12,
    context_tokens=32768,
    placement="gpu",
)


def test_engine_session_calls_native_inference(monkeypatch) -> None:
    scheduler = FakeScheduler()
    _patch_session_dependencies(monkeypatch, [GPU_MODEL, CPU_MODEL])

    with EngineSession("default-instruct", scheduler=scheduler) as session:
        assert session.resolved is not None
        assert session.resolved.model.name == "gpu-model"
        assert session.chat([{"role": "user", "content": "hi"}], max_tokens=42) == "native"

    assert scheduler.requests == [
        {
            "specialty": "default-instruct",
            "model_name": "gpu-model",
            "backend": "llama",
            "model_path": "/models/gpu-model",
            "ram_gb": 8,
            "vram_gb": 6,
            "niceness": 0,
            "max_tokens": 42,
            "gpu_layers": -999999,
            "messages_json": '[{"role": "user", "content": "hi"}]',
        }
    ]


def test_engine_session_does_not_mark_python_model_lifecycle(monkeypatch) -> None:
    scheduler = FakeScheduler()
    _patch_session_dependencies(monkeypatch, [CPU_MODEL])

    with EngineSession("default-instruct", scheduler=scheduler) as session:
        session.chat([{"role": "user", "content": "hi"}])

    assert scheduler.loaded == []
    assert scheduler.unloaded == []
    assert scheduler.evicted == []


def test_engine_session_forwards_chat_to_sandbox_endpoint(monkeypatch) -> None:
    import ageos.engine.session as session_module

    calls: list[dict[str, object]] = []

    def post(url: str, *, json: dict[str, object], timeout: float) -> FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse({"choices": [{"message": {"content": "sandbox"}}]})

    monkeypatch.setenv("AGEOS_SANDBOX", "1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_HOST", "127.0.0.1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_PORT", "8123")
    monkeypatch.setattr(session_module.requests, "post", post)
    monkeypatch.setattr(
        session_module,
        "_local_scheduler_client",
        lambda: pytest.fail("sandbox sessions must not initialize the native scheduler"),
    )

    with EngineSession("default-instruct") as session:
        assert session.chat([{"role": "user", "content": "hi"}], max_tokens=8) == "sandbox"

    assert calls == [
        {
            "url": "http://127.0.0.1:8123/v1/chat/completions",
            "json": {
                "model": "default-instruct",
                "ageos_specialty": "default-instruct",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 8,
                "stream": False,
            },
            "timeout": session_module.SANDBOX_INFERENCE_TIMEOUT_SECONDS,
        }
    ]


def test_engine_session_requires_sandbox_inference_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("AGEOS_SANDBOX", "1")
    monkeypatch.delenv("AGEOS_SANDBOX_INFERENCE_HOST", raising=False)
    monkeypatch.delenv("AGEOS_SANDBOX_INFERENCE_PORT", raising=False)

    with pytest.raises(RuntimeError, match="AGEOS_SANDBOX_INFERENCE_HOST"):
        with EngineSession("default-instruct"):
            pass


def test_engine_session_requires_matching_model(monkeypatch) -> None:
    _patch_session_dependencies(monkeypatch, [])

    with pytest.raises(RuntimeError, match="no model matches specialty"):
        with EngineSession("default-instruct", scheduler=FakeScheduler()):
            pass


def test_engine_session_chat_requires_started_session() -> None:
    with pytest.raises(RuntimeError, match="engine session is not started"):
        EngineSession("default-instruct").chat([{"role": "user", "content": "hi"}])


def test_engine_session_chat_requires_scheduler_once_resolved() -> None:
    session = EngineSession("default-instruct")
    session.resolved = ResolvedSession(model=CPU_MODEL, model_path="/models/cpu-model")

    with pytest.raises(RuntimeError, match="scheduler is not started"):
        session.chat([{"role": "user", "content": "hi"}])


def test_engine_session_embeddings_require_native_support() -> None:
    with pytest.raises(RuntimeError, match="native embeddings are not implemented"):
        EngineSession("default-instruct").embeddings(["hi"])


def test_engine_session_reports_status_callback() -> None:
    messages: list[str] = []
    EngineSession("default-instruct", status_callback=messages.append)._status("warming")
    assert messages == ["warming"]


def test_engine_session_sandbox_embeddings(monkeypatch) -> None:
    import ageos.engine.session as session_module

    monkeypatch.setenv("AGEOS_SANDBOX", "1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_HOST", "127.0.0.1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_PORT", "8123")
    monkeypatch.setattr(session_module.requests, "post", lambda *args, **kwargs: FakeResponse({"data": [{"embedding": [1.0, 2.0]}]}))

    with EngineSession("default-instruct") as session:
        assert session.embeddings(["hi"]) == [[1.0, 2.0]]


def test_engine_session_rejects_invalid_sandbox_responses(monkeypatch) -> None:
    import ageos.engine.session as session_module

    monkeypatch.setenv("AGEOS_SANDBOX", "1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_HOST", "127.0.0.1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_PORT", "8123")

    monkeypatch.setattr(session_module.requests, "post", lambda *args, **kwargs: FakeResponse({"choices": []}))
    with EngineSession("default-instruct") as session:
        with pytest.raises(RuntimeError, match="invalid chat completion response"):
            session.chat([{"role": "user", "content": "hi"}])

    monkeypatch.setattr(session_module.requests, "post", lambda *args, **kwargs: FakeResponse({"data": ["bad"]}))
    with EngineSession("default-instruct") as session:
        with pytest.raises(RuntimeError, match="invalid embeddings response"):
            session.embeddings(["hi"])

    monkeypatch.setattr(session_module.requests, "post", lambda *args, **kwargs: FakeResponse([]))
    with EngineSession("default-instruct") as session:
        with pytest.raises(RuntimeError, match="non-object JSON response"):
            session.chat([{"role": "user", "content": "hi"}])


def test_engine_session_wraps_sandbox_request_errors(monkeypatch) -> None:
    import ageos.engine.session as session_module

    monkeypatch.setenv("AGEOS_SANDBOX", "1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_HOST", "127.0.0.1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_PORT", "8123")

    def raise_request(*_args: object, **_kwargs: object) -> FakeResponse:
        raise session_module.requests.RequestException("boom")

    monkeypatch.setattr(session_module.requests, "post", raise_request)

    with EngineSession("default-instruct") as session:
        with pytest.raises(RuntimeError, match="sandbox inference request failed"):
            session.chat([{"role": "user", "content": "hi"}])


def test_engine_session_validates_environment_settings(monkeypatch) -> None:
    import ageos.engine.session as session_module

    with pytest.raises(RuntimeError, match="must be an integer"):
        session_module._parse_port("bad")
    with pytest.raises(RuntimeError, match="between 1 and 65535"):
        session_module._parse_port("70000")

    monkeypatch.setenv("AGEOS_MAX_OUTPUT_TOKENS", "bad")
    with pytest.raises(RuntimeError, match="must be an integer"):
        session_module.default_max_output_tokens()

    monkeypatch.setenv("AGEOS_MAX_OUTPUT_TOKENS", "0")
    with pytest.raises(RuntimeError, match="greater than zero"):
        session_module.default_max_output_tokens()

    assert session_module._int_or_zero("bad") == 0


class FakeRegistry:
    def __init__(self, candidates: list[ModelSpec]) -> None:
        self.candidates = candidates

    def resolve_candidates(self, *args: object, **kwargs: object) -> list[ModelSpec]:
        return self.candidates


class FakeScheduler:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.unloaded: list[str] = []
        self.evicted: list[str] = []
        self.requests: list[dict[str, object]] = []

    def resource_limits(self) -> dict[str, int]:
        return {"ram_bytes": 64 * 1024**3, "vram_bytes": 24 * 1024**3}

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
        self.loaded.append(name)

    def mark_model_unloaded(self, name: str) -> None:
        self.unloaded.append(name)

    def evict_model(self, name: str) -> None:
        self.evicted.append(name)

    def inference_chat(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        return {"content": "native"}


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class FakeDownloader:
    def ensure_model(self, model: ModelSpec) -> Path:
        return Path(f"/models/{model.name}")


def _patch_session_dependencies(
    monkeypatch,
    candidates: list[ModelSpec],
) -> None:
    import ageos.engine.session as session_module

    monkeypatch.setattr(session_module.ModelRegistry, "load_default", lambda: FakeRegistry(candidates))
    monkeypatch.setattr(
        session_module,
        "detect_hardware",
        lambda: HardwareInfo(
            ram_bytes=64 * 1024**3,
            vram_bytes=24 * 1024**3,
            free_vram_bytes=22 * 1024**3,
            gpu_vendor="nvidia",
            gpu_backend="vllm",
            gpu_backends=("vllm", "cuda-llama"),
        ),
    )
    monkeypatch.setattr(session_module, "HfDownloader", FakeDownloader)
