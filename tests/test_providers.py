from __future__ import annotations

import json

from stocktrend.providers import AnthropicMessagesClient, OpenAIResponsesClient


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


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
