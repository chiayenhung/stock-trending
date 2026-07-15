"""JSON Schema registry for versioned workflow contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .errors import ContractError
from .util import load_json


class SchemaRegistry:
    def __init__(self, schema_dir: Path):
        self.schema_dir = schema_dir
        self._schemas: Dict[str, Dict[str, Any]] = {}
        self._registry = Registry()
        for path in sorted(schema_dir.glob("*.schema.json")):
            schema = load_json(path)
            name = path.name[: -len(".schema.json")]
            self._schemas[name] = schema
            schema_id = schema.get("$id")
            if schema_id:
                self._registry = self._registry.with_resource(
                    schema_id,
                    Resource.from_contents(schema),
                )
        if not self._schemas:
            raise ContractError("no schemas found in %s" % schema_dir)

    def names(self) -> Iterable[str]:
        return sorted(self._schemas)

    def get(self, name: str) -> Dict[str, Any]:
        try:
            return self._schemas[name]
        except KeyError as exc:
            raise ContractError("unknown schema: %s" % name) from exc

    def errors(self, name: str, value: Any) -> List[str]:
        schema = self.get(name)
        validator = Draft202012Validator(
            schema,
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        messages = []
        for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            messages.append("%s: %s" % (location, error.message))
        return messages

    def validate(self, name: str, value: Any) -> None:
        messages = self.errors(name, value)
        if messages:
            raise ContractError("%s contract failed: %s" % (name, "; ".join(messages)))
