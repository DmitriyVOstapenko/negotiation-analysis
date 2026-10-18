"""Проверка 11 артефактов встречи: схемы, буквальные цитаты, meeting_id, таймкоды."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .common import (
    ANALYSIS_CONTRACTS_PATH,
    ANALYSIS_FILES,
    NaError,
    gate_state,
    meeting_dir,
    read_text,
    walk,
    write_json,
)
from .schema_runtime import load_registry, validate_contract


QUOTE_KEYS = {
    "quote",
    "source_quote",
    "related_quote",
    "prior_position_quote",
    "reaction_quote",
    "evidence_quote",
}
TIMESTAMP_KEYS = {"timestamp", "source_timestamp", "caught_timestamp", "timestamp_start", "timestamp_end", "prior_position_timestamp"}
TIMESTAMP_RE = re.compile(r"^(?:\d{2}:)?\d{2}:\d{2}$")


def load_contracts() -> dict[str, Any]:
    if not ANALYSIS_CONTRACTS_PATH.is_file():
        raise NaError(f"Контракты анализа не найдены: {ANALYSIS_CONTRACTS_PATH}")
    return load_registry(ANALYSIS_CONTRACTS_PATH)


def contract_version(registry: dict[str, Any] | None = None) -> str | None:
    registry = registry or load_contracts()
    return registry.get("x-contract-version")


def validate_schemas(meeting_path: Path, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_contracts()
    contracts = registry["x-file-contracts"]
    errors: list[dict[str, str]] = []
    checked = 0
    for filename, contract_ref in contracts.items():
        path = meeting_path / "analysis" / filename
        if not path.is_file():
            errors.append({"file": filename, "path": "$", "keyword": "required_file", "message": "файл отсутствует"})
            continue
        try:
            instance = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            errors.append({"file": filename, "path": "$", "keyword": "json", "message": str(exc)})
            continue
        checked += 1
        for item in validate_contract(instance, registry, contract_ref):
            errors.append({"file": filename, **item})
    return {
        "meeting_id": meeting_path.name,
        "schema_version": registry.get("x-contract-version"),
        "valid": not errors,
        "expected_files": len(contracts),
        "checked_files": checked,
        "errors": errors,
    }


def validate_meeting(meeting_path: Path, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    meeting_id = meeting_path.name
    analysis_dir = meeting_path / "analysis"
    transcript_path = meeting_path / "transcript.txt"
    errors: list[str] = []
    warnings: list[str] = []
    checked_quotes = 0
    parsed_files = 0

    schema_report = validate_schemas(meeting_path, registry)
    for item in schema_report["errors"]:
        errors.append(f"schema violation: {item['file']} {item['path']} [{item['keyword']}] {item['message']}")

    for required in ("meta.yaml", "transcript.txt", "review.yaml"):
        if not (meeting_path / required).is_file():
            errors.append(f"missing required file: {required}")
    if not (meeting_path / "source_packet.json").is_file():
        warnings.append("source_packet.json отсутствует (встреча зарегистрирована до появления SourcePacket)")
    if not (meeting_path / "report.md").is_file():
        warnings.append("финальный report.md ещё не подготовлен")

    transcript = read_text(transcript_path)

    for filename in ANALYSIS_FILES:
        path = analysis_dir / filename
        if not path.is_file():
            errors.append(f"missing analysis file: {filename}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            parsed_files += 1
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON {filename}: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"root of {filename} must be an object")
            continue
        if data.get("meeting_id") != meeting_id:
            errors.append(f"meeting_id mismatch in {filename}: {data.get('meeting_id')!r}")
        for key, value, json_path in walk(data):
            if key in QUOTE_KEYS and isinstance(value, str) and value:
                checked_quotes += 1
                if value not in transcript:
                    errors.append(f"quote not found in transcript: {filename} {json_path}")
            if key in TIMESTAMP_KEYS and value is not None:
                if not isinstance(value, str) or not TIMESTAMP_RE.match(value):
                    errors.append(f"invalid timestamp: {filename} {json_path}={value!r}")
            if key == "meeting_id" and isinstance(value, str) and value != meeting_id and filename == "08_risks.json":
                # риск может ссылаться на более раннюю встречу (first_seen); это не ошибка
                pass

    gate = gate_state(meeting_path)
    if gate["consent"] != "yes":
        warnings.append("шлюз согласия не пройден: consent != yes (processing_status остаётся blocked_by_gate)")
    if gate["review_status"] == "pending":
        warnings.append("human review is pending")
    speakers = gate["review"].get("speaker_mapping") if isinstance(gate["review"].get("speaker_mapping"), dict) else {}
    if speakers.get("status") != "confirmed":
        warnings.append("карта спикеров не подтверждена пользователем")

    return {
        "meeting_id": meeting_id,
        "valid": not errors,
        "required_analysis_files": len(ANALYSIS_FILES),
        "parsed_analysis_files": parsed_files,
        "schema_version": schema_report["schema_version"],
        "schema_valid": schema_report["valid"],
        "checked_quotes": checked_quotes,
        "gate_consent": gate["consent"],
        "errors": errors,
        "warnings": warnings,
    }


def run_validate(root: Path, meeting_id: str) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    report = validate_meeting(path)
    write_json(path / "analysis" / "validation_report.json", report)
    return report


def summary_lines(report: dict[str, Any]) -> list[str]:
    lines = [
        f"Встреча {report['meeting_id']}: {'валидна' if report['valid'] else 'НЕ валидна'}; "
        f"схема {report['schema_version']}: {'пройдена' if report['schema_valid'] else 'не пройдена'}; "
        f"JSON {report['parsed_analysis_files']}/{report['required_analysis_files']}; "
        f"цитат проверено: {report['checked_quotes']}; ошибок: {len(report['errors'])}; "
        f"предупреждений: {len(report['warnings'])}."
    ]
    lines.extend(f"  ошибка: {item}" for item in report["errors"][:50])
    if len(report["errors"]) > 50:
        lines.append(f"  … ещё {len(report['errors']) - 50} ошибок в analysis/validation_report.json")
    lines.extend(f"  предупреждение: {item}" for item in report["warnings"])
    return lines
