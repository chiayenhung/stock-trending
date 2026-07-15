"""Structured-output model provider adapters."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping, Optional

from .errors import ConfigurationError, ProviderError


def _schema_for_provider(schema: Dict[str, Any]) -> Dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if key not in ("$schema", "$id", "title")
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(deepcopy(schema))


def _safe_schema_name(task_name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]", "_", task_name)
    return value[:64] or "structured_output"


def _post_json(
    url: str,
    headers: Mapping[str, str],
    payload: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        request_id = exc.headers.get("request-id") or exc.headers.get("x-request-id")
        raise ProviderError(
            "provider HTTP %s%s"
            % (exc.code, " request_id=%s" % request_id if request_id else "")
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderError("provider network request failed") from exc
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError("provider returned non-JSON response") from exc
    if not isinstance(value, dict):
        raise ProviderError("provider response must be a JSON object")
    return value


class JsonModelClient(ABC):
    vendor_id: str
    model: str

    @abstractmethod
    def generate_json(
        self,
        task_name: str,
        system_prompt: str,
        payload: Dict[str, Any],
        output_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError


class OpenAIResponsesClient(JsonModelClient):
    vendor_id = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: int = 120,
        endpoint: str = "https://api.openai.com/v1/responses",
    ):
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required")
        if not model:
            raise ConfigurationError("OpenAI model is required")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.endpoint = endpoint

    def generate_json(
        self,
        task_name: str,
        system_prompt: str,
        payload: Dict[str, Any],
        output_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = {
            "model": self.model,
            "store": False,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": _safe_schema_name(task_name),
                    "strict": True,
                    "schema": _schema_for_provider(output_schema),
                }
            },
        }
        response = _post_json(
            self.endpoint,
            {
                "Authorization": "Bearer %s" % self.api_key,
                "Content-Type": "application/json",
            },
            body,
            self.timeout_seconds,
        )
        if response.get("status") not in (None, "completed"):
            raise ProviderError("OpenAI response did not complete")
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                if block.get("type") == "refusal":
                    raise ProviderError("OpenAI model refused the task")
                if block.get("type") == "output_text":
                    try:
                        value = json.loads(block["text"])
                    except (KeyError, json.JSONDecodeError) as exc:
                        raise ProviderError("OpenAI structured output was invalid") from exc
                    if not isinstance(value, dict):
                        raise ProviderError("OpenAI output must be an object")
                    return value
        raise ProviderError("OpenAI response contained no structured output")


class AnthropicMessagesClient(JsonModelClient):
    vendor_id = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: int = 120,
        max_tokens: int = 4096,
        endpoint: str = "https://api.anthropic.com/v1/messages",
    ):
        if not api_key:
            raise ConfigurationError("ANTHROPIC_API_KEY is required")
        if not model:
            raise ConfigurationError("Anthropic model is required")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.endpoint = endpoint

    def generate_json(
        self,
        task_name: str,
        system_prompt: str,
        payload: Dict[str, Any],
        output_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            ],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": _schema_for_provider(output_schema),
                }
            },
        }
        response = _post_json(
            self.endpoint,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            body,
            self.timeout_seconds,
        )
        if response.get("stop_reason") in ("refusal", "max_tokens"):
            raise ProviderError("Anthropic response stopped: %s" % response["stop_reason"])
        for block in response.get("content", []):
            if block.get("type") == "text":
                try:
                    value = json.loads(block["text"])
                except (KeyError, json.JSONDecodeError) as exc:
                    raise ProviderError("Anthropic structured output was invalid") from exc
                if not isinstance(value, dict):
                    raise ProviderError("Anthropic output must be an object")
                return value
        raise ProviderError("Anthropic response contained no structured output")


class ScriptedClient(JsonModelClient):
    """Offline deterministic stand-in used by tests and the demo."""

    def __init__(
        self,
        vendor_id: str,
        model: str,
        handlers: Mapping[str, Callable[[Dict[str, Any]], Dict[str, Any]]],
        default_handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    ):
        self.vendor_id = vendor_id
        self.model = model
        self.handlers = dict(handlers)
        self.default_handler = default_handler

    def generate_json(
        self,
        task_name: str,
        system_prompt: str,
        payload: Dict[str, Any],
        output_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        del system_prompt, output_schema
        if task_name in self.handlers:
            return deepcopy(self.handlers[task_name](deepcopy(payload)))
        if self.default_handler is not None:
            return deepcopy(self.default_handler(task_name, deepcopy(payload)))
        raise ProviderError("no scripted handler for task: %s" % task_name)


def create_provider(name: str) -> JsonModelClient:
    if name == "openai":
        return OpenAIResponsesClient(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("STOCKTREND_OPENAI_MODEL", "gpt-5.6"),
        )
    if name == "anthropic":
        return AnthropicMessagesClient(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("STOCKTREND_ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        )
    raise ConfigurationError("unknown provider: %s" % name)
