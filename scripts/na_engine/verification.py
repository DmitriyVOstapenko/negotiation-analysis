"""Проверка отчёта независимой верификации (агент evidence-verifier, режим с верификацией).

Отчёт лежит в negotiations/<id>/analysis/verification_report.json. Агент ничего не исправляет и
сам файл не пишет: он возвращает JSON, главный агент сохраняет его и запускает эту проверку.
Проверяется, что каждая находка доказательна: вердикт из допустимых, адрес поля указывает на
существующее место артефакта, цитата есть в транскрипте (тем же модулем quote_check, что и
validate), есть таймкод (или номер строки, если таймкодов в записи нет), сводка совпадает с
находками. Содержательную правоту находок движок не оценивает — это делает человек.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .common import ANALYSIS_FILES, NaError, meeting_dir, read_json_or_none, read_text, read_yaml


REPORT_FILE = "analysis/verification_report.json"
VERDICTS = ("confirmed", "contradicted", "changed_later", "unsupported", "missed")
VERDICT_LABELS = {
    "confirmed": "подтверждено",
    "contradicted": "источник противоречит",
    "changed_later": "изменено позже по ходу встречи",
    "unsupported": "нет опоры в источнике",
    "missed": "пропущено в разборе",
}
TIMESTAMP_RE = re.compile(r"^(?:\d{2}:)?\d{2}:\d{2}$")
PATH_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def resolve_path(data: Any, path: str) -> tuple[bool, Any]:
    """JSON-адрес вида $.commitments[2].deadline → (найдено, значение)."""
    if not isinstance(path, str) or not path.startswith("$"):
        return False, None
    rest = path[1:]
    position = 0
    current = data
    while position < len(rest):
        match = PATH_TOKEN_RE.match(rest, position)
        if not match:
            return False, None
        key, index = match.group(1), match.group(2)
        if key is not None:
            if not isinstance(current, dict) or key not in current:
                return False, None
            current = current[key]
        else:
            number = int(index)
            if not isinstance(current, list) or number >= len(current):
                return False, None
            current = current[number]
        position = match.end()
    return True, current


def check_report(meeting_path: Path) -> dict[str, Any]:
    from .quote_check import check_quotes

    meeting_id = meeting_path.name
    report_path = meeting_path / REPORT_FILE
    if not report_path.is_file():
        raise NaError(f"Нет отчёта проверки: negotiations/{meeting_id}/{REPORT_FILE}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {"meeting_id": meeting_id, "valid": False, "errors": [f"некорректный JSON: {exc}"], "warnings": [], "counts": {}}
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(report, dict):
        return {"meeting_id": meeting_id, "valid": False, "errors": ["корень отчёта — не объект"], "warnings": [], "counts": {}}
    if report.get("meeting_id") != meeting_id:
        errors.append(f"meeting_id {report.get('meeting_id')!r} ≠ {meeting_id!r}")
    findings = report.get("findings")
    if not isinstance(findings, list):
        errors.append("findings: ожидается список находок (пустой список — тоже ответ)")
        findings = []
    scope = report.get("scope")
    if not isinstance(scope, list) or not scope:
        warnings.append("scope не указан: неясно, какие артефакты проверялись")

    transcript = read_text(meeting_path / "transcript.txt")
    has_timestamps = bool(re.search(r"\d{1,2}:\d{2}", transcript))
    artifacts: dict[str, Any] = {}
    quotes: list[dict[str, Any]] = []
    quote_owner: list[int] = []
    counts = {verdict: 0 for verdict in VERDICTS}
    for number, finding in enumerate(findings):
        where = f"findings[{number}]"
        if not isinstance(finding, dict):
            errors.append(f"{where}: находка — не объект")
            continue
        verdict = finding.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"{where}.verdict: {verdict!r} — ожидается одно из {', '.join(VERDICTS)}")
        else:
            counts[verdict] += 1
        artifact = finding.get("artifact")
        if artifact not in ANALYSIS_FILES:
            errors.append(f"{where}.artifact: {artifact!r} — ожидается имя файла артефакта (например 07_commitments.json)")
        else:
            if artifact not in artifacts:
                artifacts[artifact] = read_json_or_none(meeting_path / "analysis" / artifact)
            data = artifacts[artifact]
            field_path = finding.get("path")
            if data is None:
                errors.append(f"{where}: артефакта {artifact} нет или он не читается")
            else:
                found, value = resolve_path(data, field_path)
                if not found:
                    errors.append(f"{where}.path: адрес {field_path!r} не найден в {artifact}")
                elif verdict == "missed" and not isinstance(value, list):
                    errors.append(f"{where}.path: для пропуска нужен адрес списка, куда относится пропущенная запись (например $.commitments)")
        quote = finding.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            errors.append(f"{where}.quote: находка без цитаты не принимается")
        else:
            timestamp = finding.get("timestamp")
            quotes.append({"file": "verification_report.json", "path": f"$.{where}.quote", "quote": quote, "claimed_start": timestamp if isinstance(timestamp, str) else None, "claimed_end": timestamp if isinstance(timestamp, str) else None, "attribution": None})
            quote_owner.append(number)
        timestamp = finding.get("timestamp")
        if timestamp is not None and (not isinstance(timestamp, str) or not TIMESTAMP_RE.match(timestamp)):
            errors.append(f"{where}.timestamp: {timestamp!r} — ожидается HH:MM:SS или MM:SS")
        if has_timestamps and not timestamp:
            errors.append(f"{where}.timestamp: в записи есть таймкоды — у находки нужен таймкод")
        if not has_timestamps and not timestamp and not isinstance(finding.get("line"), int):
            errors.append(f"{where}: в записи нет таймкодов — укажите номер строки (line)")
        if not isinstance(finding.get("explanation"), str) or not finding.get("explanation", "").strip():
            warnings.append(f"{where}.explanation: нет пояснения, почему вынесен вердикт")

    quote_result = check_quotes(transcript, quotes, read_yaml(meeting_path / "review.yaml"), []) if quotes else {"errors": [], "warnings": [], "stats": {}}
    errors.extend(quote_result["errors"])
    warnings.extend(warning for warning in quote_result["warnings"] if "атрибуц" not in warning)

    summary = report.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary: нужна сводка {checked, confirmed, contradicted, changed_later, unsupported, missed}")
    else:
        for verdict in VERDICTS[1:]:
            declared = summary.get(verdict)
            if declared != counts[verdict]:
                errors.append(f"summary.{verdict}={declared!r}, а находок с этим вердиктом {counts[verdict]}")
        checked = summary.get("checked")
        if not isinstance(checked, int) or checked < counts["confirmed"] + counts["contradicted"] + counts["changed_later"] + counts["unsupported"]:
            errors.append("summary.checked: число проверенных записей не меньше числа находок по ним")
    return {
        "meeting_id": meeting_id,
        "valid": not errors,
        "report": f"negotiations/{meeting_id}/{REPORT_FILE}",
        "counts": counts,
        "summary": summary if isinstance(summary, dict) else None,
        "errors": errors,
        "warnings": warnings,
    }


def run_check(root: Path, meeting_id: str) -> dict[str, Any]:
    return check_report(meeting_dir(root, meeting_id))


def check_lines(result: dict[str, Any]) -> list[str]:
    counts = result.get("counts") or {}
    head = ", ".join(f"{VERDICT_LABELS[verdict]} {counts.get(verdict, 0)}" for verdict in VERDICTS[1:])
    summary = result.get("summary") or {}
    lines = [
        f"Отчёт проверки {result['meeting_id']}: " + ("находки доказательны." if result["valid"] else f"ошибок {len(result['errors'])} — отчёт не принимается."),
        f"  Проверено записей: {summary.get('checked', '—')}; {head}.",
    ]
    lines += [f"  - {error}" for error in result["errors"][:30]]
    lines += [f"  · {warning}" for warning in result["warnings"][:15]]
    return lines
