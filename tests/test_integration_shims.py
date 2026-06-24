from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from ageos.integrations.anthropic_shim import AgeosAnthropic


def test_anthropic_shim_uses_native_session_even_when_api_base_is_set(monkeypatch) -> None:
    monkeypatch.setenv("AGEOS_API_BASE_URL", "http://127.0.0.1:8000")

    with patch("ageos.integrations.anthropic_shim.EngineSession") as session_cls:
        session = session_cls.return_value.__enter__.return_value
        session.chat.return_value = "native"
        response = AgeosAnthropic(speciality="default-instruct", niceness=2).messages.create(
            model="ageos-local",
            system="be brief",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        )

    session_cls.assert_called_once_with("default-instruct", niceness=2)
    session.chat.assert_called_once_with(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert response.content == [{"type": "text", "text": "native"}]


def test_langchain_wrapper_uses_native_session(monkeypatch) -> None:
    from ageos.integrations import langchain as langchain_module

    monkeypatch.setattr(langchain_module, "BaseChatModel", FakeBaseChatModel)
    monkeypatch.setattr(langchain_module, "HumanMessage", FakeHumanMessage)
    monkeypatch.setattr(langchain_module, "SystemMessage", FakeSystemMessage)
    monkeypatch.setattr(langchain_module, "AIMessage", lambda content: SimpleNamespace(content=content))
    monkeypatch.setattr(langchain_module, "ChatGeneration", lambda message: SimpleNamespace(message=message))
    monkeypatch.setattr(langchain_module, "ChatResult", lambda generations: SimpleNamespace(generations=generations))

    with patch("ageos.integrations.langchain.EngineSession") as session_cls:
        session = session_cls.return_value.__enter__.return_value
        session.chat.return_value = "native"
        model = langchain_module.AgeosChatModel(speciality="default-instruct", niceness=4)
        result = model._generate([FakeSystemMessage("rules"), FakeHumanMessage("hi")])

    session_cls.assert_called_once_with("default-instruct", niceness=4)
    session.chat.assert_called_once_with(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert result.generations[0].message.content == "native"
    assert model._llm_type == "ageos"


def test_langchain_wrapper_requires_optional_dependency(monkeypatch) -> None:
    from ageos.integrations import langchain as langchain_module

    monkeypatch.setattr(langchain_module, "BaseChatModel", object)

    with pytest.raises(ImportError, match="install ageos\\[langchain\\]"):
        langchain_module.AgeosChatModel()


def test_langchain_convert_message_defaults_to_assistant_role(monkeypatch) -> None:
    from ageos.integrations import langchain as langchain_module

    monkeypatch.setattr(langchain_module, "HumanMessage", FakeHumanMessage)
    monkeypatch.setattr(langchain_module, "SystemMessage", FakeSystemMessage)

    assistant_like = type("AssistantMessage", (), {"content": "done"})()
    assert langchain_module._convert_message(assistant_like) == {"role": "assistant", "content": "done"}


class FakeHumanMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeSystemMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeBaseChatModel:
    def __init__(self, **_kwargs: object) -> None:
        pass
