"""Проверка артефактов встречи: схемы, цитаты (уровень совпадения, склейки, атрибуция, таймкоды), meeting_id.

Состав обязательных артефактов задаёт профиль разбора (analysis_profile.py): full — все 11,
light — 01, 07, 08; артефакт вне профиля, если его нет, — не ошибка. Строгая проверка цитат —
quote_check.py.
"""

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
from .analysis_profile import profile_error, profile_summary
from .quote_check import QUOTE_KEYS as QUOTE_FIELDS
from .quote_check import check_quotes, collect_quotes
from .schema_runtime import load_registry, validate_contract


QUOTE_KEYS = set(QUOTE_FIELDS)  # поля-цитаты; проверка — quote_check.check_quotes
TIMESTAMP_KEYS = {"timestamp", "source_timestamp", "caught_timestamp", "timestamp_start", "timestamp_end", "prior_position_timestamp"}
TIMESTAMP_RE = re.compile(r"^(?:\d{2}:)?\d{2}:\d{2}$")


def load_contracts() -> dict[str, Any]:
    if not ANALYSIS_CONTRACTS_PATH.is_file():
        raise NaError(f"Контракты анализа не найдены: {ANALYSIS_CONTRACTS_PATH}")
    return load_registry(ANALYSIS_CONTRACTS_PATH)


def contract_version(registry: dict[str, Any] | None = None) -> str | None:
    registry = registry or load_contracts()
    return registry.get("x-contract-version")


def validate_schemas(meeting_path: Path, registry: dict[str, Any] | None = None, required: set[str] | None = None) -> dict[str, Any]:
    """required — обязательные по профилю файлы; отсутствующий файл вне профиля пропускается."""
    registry = registry or load_contracts()
    contracts = registry["x-file-contracts"]
    errors: list[dict[str, str]] = []
    checked = 0
    for filename, contract_ref in contracts.items():
        path = meeting_path / "analysis" / filename
        if not path.is_file():
            if required is not None and filename not in required:
                continue
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
    parsed_files = 0
    profile = profile_summary(meeting_path)
    required_files = set(profile["required"])
    quote_items: list[dict[str, Any]] = []
    participants: list[Any] = []
    if profile_error(meeting_path):
        errors.append(profile_error(meeting_path))

    schema_report = validate_schemas(meeting_path, registry, required_files)
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
            if filename in required_files:
                errors.append(f"missing analysis file: {filename}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            parsed_files += 1 if filename in required_files else 0
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON {filename}: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"root of {filename} must be an object")
            continue
        if data.get("meeting_id") != meeting_id:
            errors.append(f"meeting_id mismatch in {filename}: {data.get('meeting_id')!r}")
        if filename == "01_extraction.json" and isinstance(data.get("participants"), list):
            participants = data["participants"]
        quote_items.extend(collect_quotes(filename, data))
        for key, value, json_path in walk(data):
            if key in TIMESTAMP_KEYS and value is not None:
                if not isinstance(value, str) or not TIMESTAMP_RE.match(value):
                    errors.append(f"invalid timestamp: {filename} {json_path}={value!r}")
            if key == "meeting_id" and isinstance(value, str) and value != meeting_id and filename == "08_risks.json":
                # риск может ссылаться на более раннюю встречу (first_seen); это не ошибка
                pass

    gate = gate_state(meeting_path)
    quotes = check_quotes(transcript, quote_items, gate["review"], participants)
    errors.extend(quotes["errors"])
    if gate["consent"] != "yes":
        warnings.append("шлюз согласия не пройден: consent != yes (processing_status остаётся blocked_by_gate)")
    if gate["review_status"] == "pending":
        warnings.append("ручная проверка встречи ещё не проведена (review_status: pending)")
    speakers = gate["review"].get("speaker_mapping") if isinstance(gate["review"].get("speaker_mapping"), dict) else {}
    if speakers.get("status") != "confirmed":
        warnings.append("карта спикеров не подтверждена пользователем")
    warnings.extend(quotes["warnings"])

    return {
        "meeting_id": meeting_id,
        "valid": not errors,
        "analysis_profile": profile["profile"],
        "required_analysis_files": len(profile["required"]),
        "skipped_by_profile": [name for name in profile["skipped_by_profile"] if name not in profile["present_outside_profile"]],
        "present_outside_profile": profile["present_outside_profile"],
        "parsed_analysis_files": parsed_files,
        "schema_version": schema_report["schema_version"],
        "schema_valid": schema_report["valid"],
        "checked_quotes": quotes["stats"]["checked"],
        "quote_stats": quotes["stats"],
        "source_quality": quotes["source_quality"],
        "timestamp_tolerance_sec": quotes["timestamp_tolerance_sec"],
        "gate_consent": gate["consent"],
        "errors": errors,
        "warnings": warnings,
        "quote_checks": quotes["quote_checks"],
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
    stats = report.get("quote_stats") or {}
    quality = report.get("source_quality") or {}
    if stats:
        lines.append(
            f"  цитаты: буквально {stats['exact']}, после нормализации {stats['normalized']}, с пропуском «…» {stats['ellipsis']}; "
            f"не найдено {stats['not_found']}, склеек {stats['spliced']}; атрибуция: совпала {stats['attribution_matched']} из {stats['attribution_checked']}, "
            f"расхождений {stats['attribution_mismatched']}, не проверено {stats['attribution_unverified']}; "
            f"таймкоды (допуск ±{report.get('timestamp_tolerance_sec')} с): дрейф {stats['timestamp_drift']}, на другую реплику {stats['timestamp_other_reply']}."
        )
    if quality:
        lines.append(
            f"  источник: таймкоды {'есть' if quality.get('has_timestamps') else 'нет'}; разметка говорящих "
            f"{'есть (' + ', '.join(quality.get('speaker_labels', [])) + ')' if quality.get('has_speaker_labels') else 'нет'}; "
            f"карта спикеров: {quality.get('speaker_map_status')}; профиль разбора: {report.get('analysis_profile', 'full')}"
            + (f" (не выполнялись по профилю: {', '.join(report['skipped_by_profile'])})" if report.get("skipped_by_profile") else "")
            + "."
        )
    lines.extend(f"  ошибка: {item}" for item in report["errors"][:50])
    if len(report["errors"]) > 50:
        lines.append(f"  … ещё {len(report['errors']) - 50} ошибок в analysis/validation_report.json")
    lines.extend(f"  предупреждение: {item}" for item in report["warnings"])
    return lines
