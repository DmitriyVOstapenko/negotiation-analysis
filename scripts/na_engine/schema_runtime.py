#!/usr/bin/env python3
"""Минимальный исполняемый поднабор JSON Schema без внешних зависимостей.

Поддерживаются только конструкции, используемые в core/contracts/contracts.schema.json
и core/contracts/knowledge_event.schema.json.
Это намеренное ограничение: проект остаётся переносимым, а неизвестное ключевое
слово схемы считается ошибкой разработки, а не молча игнорируется.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SUPPORTED_KEYWORDS = {
    "$id",
    "$schema",
    "$ref",
    "$defs",
    "title",
    "description",
    "type",
    "const",
    "enum",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "anyOf",
    "oneOf",
}


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_pointer(root: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise ValueError(f"Поддерживаются только локальные $ref, получено: {pointer}")
    current = root
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[token]
    return current


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise ValueError(f"Неподдерживаемый type в схеме: {expected}")


def _error(path: str, keyword: str, message: str) -> dict[str, str]:
    return {"path": path, "keyword": keyword, "message": message}


def validate_instance(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[dict[str, str]]:
    """Вернуть список нарушений схемы; пустой список означает успех."""
    unknown = set(schema) - SUPPORTED_KEYWORDS
    if unknown:
        raise ValueError(f"Неподдерживаемые ключи схемы в {path}: {sorted(unknown)}")

    errors: list[dict[str, str]] = []

    if "$ref" in schema:
        target = _resolve_pointer(root_schema, schema["$ref"])
        errors.extend(validate_instance(instance, target, root_schema, path))
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        if siblings:
            errors.extend(validate_instance(instance, siblings, root_schema, path))
        return errors

    if "anyOf" in schema:
        variants = [validate_instance(instance, item, root_schema, path) for item in schema["anyOf"]]
        if not any(not variant for variant in variants):
            return [_error(path, "anyOf", "значение не соответствует ни одному допустимому варианту")]
        return []

    if "oneOf" in schema:
        matched = sum(not validate_instance(instance, item, root_schema, path) for item in schema["oneOf"])
        if matched != 1:
            return [_error(path, "oneOf", f"ожидался ровно один вариант, совпало: {matched}")]
        return []

    if "type" in schema:
        expected = schema["type"]
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(instance, item) for item in expected_types):
            return [
                _error(
                    path,
                    "type",
                    f"ожидался type={expected_types}, получен {_type_name(instance)}",
                )
            ]

    if "const" in schema and instance != schema["const"]:
        errors.append(_error(path, "const", f"ожидалось {schema['const']!r}, получено {instance!r}"))
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(_error(path, "enum", f"недопустимое значение {instance!r}"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(_error(path, "minLength", "строка короче допустимого"))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(_error(path, "maxLength", "строка длиннее допустимого"))
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(_error(path, "pattern", f"строка не соответствует {schema['pattern']!r}"))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(_error(path, "minimum", f"значение меньше {schema['minimum']}"))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(_error(path, "maximum", f"значение больше {schema['maximum']}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(_error(path, "minItems", f"элементов меньше {schema['minItems']}"))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(_error(path, "maxItems", f"элементов больше {schema['maxItems']}"))
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(serialized) != len(set(serialized)):
                errors.append(_error(path, "uniqueItems", "массив содержит дубликаты"))
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate_instance(item, schema["items"], root_schema, f"{path}[{index}]"))

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                errors.append(_error(f"{path}.{required}", "required", "обязательное поле отсутствует"))
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(validate_instance(value, properties[key], root_schema, child_path))
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(_error(child_path, "additionalProperties", "неизвестное поле"))
            elif isinstance(additional, dict):
                errors.extend(validate_instance(value, additional, root_schema, child_path))

    return errors


def validate_contract(
    instance: Any,
    registry: dict[str, Any],
    contract_ref: str,
) -> list[dict[str, str]]:
    return validate_instance(instance, {"$ref": contract_ref}, registry)

