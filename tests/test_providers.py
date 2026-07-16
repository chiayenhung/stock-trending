from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from stocktrend.providers import (
    AnthropicMessagesClient,
    ClaudeSubscriptionClient,
    CodexSubscriptionClient,
    OpenAIResponsesClient,
    _schema_for_provider,
    create_host_provider_pair,
)


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


def test_provider_schema_adds_types_required_for_const_and_enum() -> None:
    schema = _schema_for_provider(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "schema_version": {"const": "1.0.0"},
                "verdict": {"enum": ["pass", "reject"]},
                "ids": {"type": "array", "uniqueItems": True},
            },
        }
    )

    assert "$schema" not in schema
    assert schema["properties"]["schema_version"] == {
        "const": "1.0.0",
        "type": "string",
    }
    assert schema["properties"]["verdict"] == {
        "enum": ["pass", "reject"],
        "type": "string",
    }
    assert "uniqueItems" not in schema["properties"]["ids"]


def test_openai_uses_responses_structured_output(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, payload, timeout_seconds):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout_seconds,
            }
        )
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"answer": "ok"}),
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr("stocktrend.providers._post_json", fake_post)
    client = OpenAIResponsesClient("secret", "gpt-test")
    result = client.generate_json("test", "system", {"value": 1}, SCHEMA)
    assert result == {"answer": "ok"}
    assert captured["url"].endswith("/v1/responses")
    assert captured["payload"]["store"] is False
    assert captured["payload"]["text"]["format"]["type"] == "json_schema"
    assert captured["payload"]["text"]["format"]["strict"] is True


def test_anthropic_uses_messages_structured_output(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, payload, timeout_seconds):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout_seconds,
            }
        )
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps({"answer": "ok"})}],
        }

    monkeypatch.setattr("stocktrend.providers._post_json", fake_post)
    client = AnthropicMessagesClient("secret", "claude-test")
    result = client.generate_json("test", "system", {"value": 1}, SCHEMA)
    assert result == {"answer": "ok"}
    assert captured["url"].endswith("/v1/messages")
    assert captured["payload"]["output_config"]["format"]["type"] == "json_schema"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"


def test_codex_subscription_requires_chatgpt_auth_and_strips_api_keys(
    monkeypatch,
) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "CODEX_API_KEY" not in kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert "STOCKTREND_TIINGO_API_TOKEN" not in kwargs["env"]
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Logged in using ChatGPT\n",
                stderr="",
            )
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps({"answer": "ok"}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("STOCKTREND_TIINGO_API_TOKEN", "must-not-leak")
    monkeypatch.setattr("stocktrend.providers.subprocess.run", fake_run)
    client = CodexSubscriptionClient(model="gpt-test")
    assert client.generate_json("test", "system", {"value": 1}, SCHEMA) == {
        "answer": "ok"
    }
    generation = calls[1][0]
    assert "--sandbox" in generation
    assert generation[generation.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in generation


def test_claude_subscription_requires_non_api_auth_and_disables_tools(
    monkeypatch,
) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "CODEX_API_KEY" not in kwargs["env"]
        assert "STOCKTREND_TIINGO_API_TOKEN" not in kwargs["env"]
        if command[1:3] == ["auth", "status"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"loggedIn": True, "authMethod": "oauth", "apiProvider": "firstParty"}
                ),
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": {"answer": "ok"}}),
            stderr="",
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-leak")
    monkeypatch.setenv("STOCKTREND_TIINGO_API_TOKEN", "must-not-leak")
    monkeypatch.setattr("stocktrend.providers.subprocess.run", fake_run)
    client = ClaudeSubscriptionClient(model="sonnet")
    assert client.generate_json("test", "system", {"value": 1}, SCHEMA) == {
        "answer": "ok"
    }
    generation = calls[1][0]
    assert "--safe-mode" in generation
    assert generation[generation.index("--tools") + 1] == ""
    assert generation[generation.index("--permission-mode") + 1] == "dontAsk"


def test_host_pair_uses_subscription_producer_and_opposite_api_validator(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    codex_producer, claude_validator = create_host_provider_pair("codex")
    assert isinstance(codex_producer, CodexSubscriptionClient)
    assert isinstance(claude_validator, AnthropicMessagesClient)
    claude_producer, openai_validator = create_host_provider_pair("claude")
    assert isinstance(claude_producer, ClaudeSubscriptionClient)
    assert isinstance(openai_validator, OpenAIResponsesClient)
