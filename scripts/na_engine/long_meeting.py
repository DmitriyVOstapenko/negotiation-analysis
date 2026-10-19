"""Длинные встречи: план частей по границам реплик, проверка и слияние частичных артефактов.

Зачем. В Claude Code и Cowork главный агент не читает длинный транскрипт целиком: артефакты
01–08 по частям готовит агент transcript-analyst, главный держит разговор и читает только JSON.
Движок даёт детерминированную опору этому пути:

- plan — делит транскрипт на части по границам реплик (реплика не режется), считает объём и
  длительность и говорит, длинная ли встреча; содержимое транскрипта не печатает;
- check — проверяет файлы частей: контракт артефакта, цитаты (тем же модулем quote_check, что и
  validate) и то, что опорная цитата лежит в своей части;
- merge — сводит части одного артефакта в итоговый analysis/<артефакт>.json: списки
  объединяются, точные дубликаты снимаются, счётчики 05 суммируются, доли речи взвешиваются по
  объёму частей; поля, которые описывают встречу целиком (исход, вывод, общий тон), перечисляются
  в review_fields — их главный агент утверждает по итоговому JSON.

Раскладка: negotiations/<id>/analysis/parts/plan.json и
analysis/parts/<NN>_<имя>.part-<K>.json (контракт тот же, что у итогового файла).
Каталог parts/ не входит в analysis_bundle.json (bundle упаковывает только analysis/*.json).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .common import (
    ANALYSIS_FILES,
    NaError,
    canonical_json,
    meeting_dir,
    now_iso,
    read_json_or_none,
    read_text,
    read_yaml,
    sha256_text,
    write_json,
)
from .source import parse_segments, speaker_labels


PARTS_DIR = "analysis/parts"
PLAN_FILE = "analysis/parts/plan.json"
PART_FILE_RE = re.compile(r"^(?P<stem>\d{2}_[a-z_]+)\.part-(?P<part>\d{1,3})\.json$")

DEFAULT_PART_CHARS = 25_000
CONTEXT_REPLICAS = 3
# Порог «длинной» встречи. 60 минут живого разговора — это обычно 45–70 тыс. знаков русской
# расшифровки; такой текст целиком в контексте главного агента вытесняет разговор с
# пользователем и сам разбор. Любой из трёх признаков достаточен.
LONG_THRESHOLDS = {"duration_min": 60, "chars": 50_000, "replicas": 500}

# Цитаты, которые обязаны лежать в своей части: опора самого пункта. Прежняя позиция
# уступки, связанная и ответная реплики могут прозвучать раньше или позже — для них
# допустим весь транскрипт (предупреждение, если вне части).
STRICT_RANGE_KEYS = {"quote", "source_quote", "evidence_quote"}

# Списки сущностей 01: одна запись на ключ; при расхождении остаётся запись с более высокой
# уверенностью (при равной — из более ранней части), расхождение попадает в conflicts.
KEYED_ENTITY_LISTS = {
    ("01_extraction", "participants"): "person_id",
    ("01_extraction", "companies"): "id",
    ("01_extraction", "topics"): "id",
}
CONFIDENCE_RANK = {"certain": 3, "probable": 2, "possible": 1, "speculative": 0}
# Поля, которые описывают встречу целиком: значение по частям — только черновик.
WHOLE_MEETING_FIELDS = {
    "01_extraction": ["outcome", "outcome_summary", "excluded_source_range"],
    "03_manipulations": ["conclusion"],
    "05_power": ["dominant_speaker", "framing_control"],
    "06_emotions": ["overall_summary.overall", "overall_summary.general_trend"],
}
JOIN_TEXT_FIELDS = {("05_power", "evidence_note"), ("06_emotions", "limitations")}


# ---------------------------------------------------------------------------
# Время и реплики
# ---------------------------------------------------------------------------


def _seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    pieces = value.strip().split(":")
    if len(pieces) not in (2, 3) or not all(piece.isdigit() for piece in pieces):
        return None
    numbers = [int(piece) for piece in pieces]
    return numbers[0] * 60 + numbers[1] if len(numbers) == 2 else numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def _replicas(text: str) -> list[dict[str, Any]]:
    """Реплики транскрипта: строка с таймкодом или меткой говорящего открывает новую реплику,
    строка без них продолжает предыдущую. Пустые строки относятся к реплике перед ними."""
    segments = parse_segments(text)
    labels = set(speaker_labels(segments))
    lines = text.splitlines()
    replicas: list[dict[str, Any]] = []
    for segment in segments:
        opens = bool(segment.get("timestamp")) or (segment.get("speaker") in labels)
        if opens or not replicas:
            replicas.append({"line_from": segment["line"], "timestamps": [], "speaker": segment.get("speaker") if segment.get("speaker") in labels else None})
        if segment.get("timestamp"):
            replicas[-1]["timestamps"].append(segment["timestamp"])
    for index, replica in enumerate(replicas):
        next_start = replicas[index + 1]["line_from"] if index + 1 < len(replicas) else len(lines) + 1
        replica["line_to"] = next_start - 1
        replica["chars"] = sum(len(line) + 1 for line in lines[replica["line_from"] - 1 : replica["line_to"]])
    return replicas


def _transcript(meeting_path: Path) -> str:
    path = meeting_path / "transcript.txt"
    if not path.is_file():
        raise NaError(f"Нет транскрипта встречи: {meeting_path.name}/transcript.txt")
    return read_text(path)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def build_plan(text: str, meeting_id: str, max_chars: int = DEFAULT_PART_CHARS, context: int = CONTEXT_REPLICAS) -> dict[str, Any]:
    if max_chars < 1000:
        raise NaError("--max-chars: не меньше 1000 знаков на часть")
    replicas = _replicas(text)
    total_chars = len(text)
    seconds = [value for replica in replicas for value in (_seconds(ts) for ts in replica["timestamps"]) if value is not None]
    duration_min = round((max(seconds) - min(seconds)) / 60, 1) if len(seconds) >= 2 else None

    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, replica in enumerate(replicas):
        if current and size + replica["chars"] > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += replica["chars"]
    if current:
        groups.append(current)

    parts: list[dict[str, Any]] = []
    for number, group in enumerate(groups, start=1):
        first, last = replicas[group[0]], replicas[group[-1]]
        stamps = [ts for index in group for ts in replicas[index]["timestamps"]]
        context_index = max(0, group[0] - context)
        parts.append(
            {
                "part": number,
                "line_from": first["line_from"],
                "line_to": last["line_to"],
                "context_from_line": replicas[context_index]["line_from"] if number > 1 and context > 0 else None,
                "timestamp_from": stamps[0] if stamps else None,
                "timestamp_to": stamps[-1] if stamps else None,
                "replicas": len(group),
                "chars": sum(replicas[index]["chars"] for index in group),
                "oversized_replica": len(group) == 1 and first["chars"] > max_chars,
            }
        )

    reasons = []
    if duration_min is not None and duration_min >= LONG_THRESHOLDS["duration_min"]:
        reasons.append(f"длительность {duration_min} мин ≥ {LONG_THRESHOLDS['duration_min']}")
    if total_chars >= LONG_THRESHOLDS["chars"]:
        reasons.append(f"объём {total_chars} знаков ≥ {LONG_THRESHOLDS['chars']}")
    if len(replicas) >= LONG_THRESHOLDS["replicas"]:
        reasons.append(f"реплик {len(replicas)} ≥ {LONG_THRESHOLDS['replicas']}")
    return {
        "meeting_id": meeting_id,
        "created_at": now_iso(),
        "transcript_sha256": sha256_text(text),
        "total_lines": len(text.splitlines()),
        "total_chars": total_chars,
        "replicas": len(replicas),
        "has_timestamps": bool(seconds),
        "duration_min": duration_min,
        "thresholds": dict(LONG_THRESHOLDS),
        "long": bool(reasons),
        "long_reasons": reasons,
        "max_chars_per_part": max_chars,
        "context_replicas": context,
        "parts": parts,
    }


def run_plan(root: Path, meeting_id: str, max_chars: int | None = None, context: int | None = None) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    plan = build_plan(_transcript(path), meeting_id, max_chars or DEFAULT_PART_CHARS, CONTEXT_REPLICAS if context is None else context)
    write_json(path / PLAN_FILE, plan)
    plan["output"] = f"negotiations/{meeting_id}/{PLAN_FILE}"
    return plan


def plan_lines(plan: dict[str, Any]) -> list[str]:
    duration = f"~{plan['duration_min']} мин по таймкодам" if plan["duration_min"] is not None else "длительность неизвестна (нет таймкодов)"
    lines = [
        f"Встреча {plan['meeting_id']}: {plan['replicas']} реплик, {plan['total_chars']} знаков, {duration}.",
        ("Длинная встреча: " + "; ".join(plan["long_reasons"]) + ". Артефакты 01–08 — по частям (агент transcript-analyst).")
        if plan["long"]
        else "Встреча не длинная: разбор обычным порядком.",
        f"Частей: {len(plan['parts'])} (до {plan['max_chars_per_part']} знаков, границы — по репликам). План: {plan['output']}.",
    ]
    for part in plan["parts"]:
        span = f"{part['timestamp_from']}–{part['timestamp_to']}" if part["timestamp_from"] else "без таймкодов"
        context = f"; контекст с {part['context_from_line']}" if part["context_from_line"] else ""
        note = "; одна реплика больше лимита" if part["oversized_replica"] else ""
        lines.append(f"  часть {part['part']}: строки {part['line_from']}–{part['line_to']}{context}, {span}, {part['replicas']} реплик, {part['chars']} знаков{note}")
    return lines


def _load_plan(meeting_path: Path, text: str) -> tuple[dict[str, Any] | None, list[str]]:
    plan = read_json_or_none(meeting_path / PLAN_FILE)
    warnings: list[str] = []
    if not isinstance(plan, dict) or not isinstance(plan.get("parts"), list):
        return None, ["плана частей нет (na.py parts plan): границы частей не проверяются, доли речи сводятся без весов"]
    if plan.get("transcript_sha256") != sha256_text(text):
        warnings.append("транскрипт изменился после построения плана частей — постройте план заново")
    return plan, warnings


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _part_files(meeting_path: Path) -> list[tuple[Path, str, int]]:
    directory = meeting_path / PARTS_DIR
    found: list[tuple[Path, str, int]] = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            match = PART_FILE_RE.match(path.name)
            if match and f"{match.group('stem')}.json" in ANALYSIS_FILES:
                found.append((path, match.group("stem"), int(match.group("part"))))
    return sorted(found, key=lambda item: (item[1], item[2]))


def _participants(meeting_path: Path) -> list[Any]:
    people: list[Any] = []
    candidates = [meeting_path / "analysis" / "01_extraction.json"] + [path for path, stem, _ in _part_files(meeting_path) if stem == "01_extraction"]
    for path in candidates:
        data = read_json_or_none(path)
        if isinstance(data, dict) and isinstance(data.get("participants"), list):
            people.extend(data["participants"])
    return people


def _in_text(quote: str, text: str) -> bool:
    from .quote_check import normalize

    return quote in text or (normalize(quote) and normalize(quote) in normalize(text))


def check_part_file(path: Path, stem: str, part_no: int | None, meeting_id: str, text: str, plan: dict[str, Any] | None, review: dict[str, Any], participants: list[Any], registry: dict[str, Any]) -> dict[str, Any]:
    from .quote_check import check_quotes, collect_quotes
    from .schema_runtime import validate_contract

    errors: list[str] = []
    warnings: list[str] = []
    name = path.name
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {"file": name, "artifact": f"{stem}.json", "part": part_no, "valid": False, "errors": [f"некорректный JSON: {exc}"], "warnings": []}
    if not isinstance(data, dict):
        return {"file": name, "artifact": f"{stem}.json", "part": part_no, "valid": False, "errors": ["корень файла — не объект"], "warnings": []}
    if data.get("meeting_id") != meeting_id:
        errors.append(f"meeting_id {data.get('meeting_id')!r} ≠ {meeting_id!r}")
    contract_ref = registry["x-file-contracts"].get(f"{stem}.json")
    if contract_ref:
        for item in validate_contract(data, registry, contract_ref):
            message = f"контракт: {item['path']} [{item['keyword']}] {item['message']}"
            # в части список может быть пуст (темы прозвучали в другой части); minItems проверяется у итога
            (warnings if item["keyword"] == "minItems" and part_no is not None else errors).append(message)

    quotes = collect_quotes(name, data)
    result = check_quotes(text, quotes, review, participants)
    errors.extend(result["errors"])
    warnings.extend(result["warnings"])

    part = None
    if plan and part_no is not None:
        part = next((entry for entry in plan["parts"] if entry.get("part") == part_no), None)
        if part is None:
            errors.append(f"части {part_no} нет в плане (частей: {len(plan['parts'])})")
    if part:
        lines = text.splitlines()
        part_text = "\n".join(lines[part["line_from"] - 1 : part["line_to"]])
        for item, record in zip(quotes, result["quote_checks"]):
            if record.get("status") != "found":
                continue
            line = record.get("line")
            if isinstance(line, int) and part["line_from"] <= line <= part["line_to"]:
                continue
            if _in_text(item["quote"], part_text):
                continue
            key = item["path"].rsplit(".", 1)[-1]
            message = f"цитата вне своей части (часть {part_no}: строки {part['line_from']}–{part['line_to']}, найдена в строке {line}): {name} {item['path']}"
            (errors if key in STRICT_RANGE_KEYS else warnings).append(message)

    items = {key: len(value) for key, value in data.items() if isinstance(value, list)}
    return {"file": name, "artifact": f"{stem}.json", "part": part_no, "valid": not errors, "items": items, "quotes": result["stats"].get("checked", len(quotes)), "errors": errors, "warnings": warnings}


def run_check(root: Path, meeting_id: str, file_name: str | None = None) -> dict[str, Any]:
    from .validate import load_contracts

    path = meeting_dir(root, meeting_id)
    text = _transcript(path)
    plan, plan_warnings = _load_plan(path, text)
    review = read_yaml(path / "review.yaml")
    participants = _participants(path)
    registry = load_contracts()
    targets: list[tuple[Path, str, int | None]] = []
    if file_name:
        candidate = Path(file_name)
        if not candidate.is_absolute():
            candidate = path / PARTS_DIR / candidate.name if not (path / file_name).is_file() else path / file_name
        if not candidate.is_file():
            raise NaError(f"Файл части не найден: {file_name}")
        match = PART_FILE_RE.match(candidate.name)
        if match:
            targets.append((candidate, match.group("stem"), int(match.group("part"))))
        else:
            stem_match = re.match(r"^(\d{2}_[a-z_]+)\.json$", candidate.name)
            targets.append((candidate, stem_match.group(1) if stem_match else candidate.stem, None))
    else:
        targets = list(_part_files(path))
        proposal = path / PARTS_DIR / "speaker_map_proposal.json"
        if proposal.is_file():
            targets.append((proposal, "speaker_map_proposal", None))
    if not targets:
        raise NaError(f"Нет файлов частей в negotiations/{meeting_id}/{PARTS_DIR}/ (ожидаются <NN>_<имя>.part-<K>.json)")
    files = [check_part_file(target, stem, part_no, meeting_id, text, plan, review, participants, registry) for target, stem, part_no in targets]
    return {
        "meeting_id": meeting_id,
        "valid": all(item["valid"] for item in files),
        "files": files,
        "errors_total": sum(len(item["errors"]) for item in files),
        "warnings": plan_warnings,
    }


def check_lines(result: dict[str, Any]) -> list[str]:
    lines = [f"Проверка частей {result['meeting_id']}: " + ("все файлы проходят." if result["valid"] else f"ошибок {result['errors_total']}.")]
    lines += [f"  ! {warning}" for warning in result["warnings"]]
    for item in result["files"]:
        status = "ок" if item["valid"] else f"ошибок {len(item['errors'])}"
        lines.append(f"  {item['file']}: {status}" + (f", предупреждений {len(item['warnings'])}" if item["warnings"] else ""))
        lines += [f"    - {error}" for error in item["errors"][:20]]
        lines += [f"    · {warning}" for warning in item["warnings"][:10]]
    return lines


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _weights(plan: dict[str, Any] | None, part_numbers: list[int]) -> dict[int, float]:
    by_part = {entry.get("part"): entry.get("chars") for entry in (plan or {}).get("parts", []) if isinstance(entry, dict)}
    if all(isinstance(by_part.get(number), (int, float)) and by_part[number] > 0 for number in part_numbers):
        return {number: float(by_part[number]) for number in part_numbers}
    return {number: 1.0 for number in part_numbers}


def _merge_keyed(stem: str, field: str, key: str, values: list[tuple[int, list[Any]]], conflicts: list[dict[str, Any]]) -> list[Any]:
    chosen: dict[Any, tuple[int, dict[str, Any]]] = {}
    order: list[Any] = []
    for part_no, items in values:
        for item in items:
            if not isinstance(item, dict) or item.get(key) in (None, ""):
                order.append(("__raw__", part_no, len(order)))
                chosen[order[-1]] = (part_no, item)
                continue
            ident = item[key]
            if ident not in chosen:
                chosen[ident] = (part_no, item)
                order.append(ident)
                continue
            kept_part, kept = chosen[ident]
            if canonical_json(kept) == canonical_json(item):
                continue
            better = CONFIDENCE_RANK.get(item.get("confidence"), -1) > CONFIDENCE_RANK.get(kept.get("confidence"), -1)
            if better:
                chosen[ident] = (part_no, item)
            conflicts.append({"field": field, "key": f"{key}={ident}", "parts": [kept_part, part_no], "kept_from_part": part_no if better else kept_part, "reason": "записи различаются; оставлена с большей уверенностью (при равной — из более ранней части)"})
    return [chosen[ident][1] for ident in order]


def _merge_list(field: str, values: list[tuple[int, list[Any]]], renamed: list[dict[str, Any]]) -> tuple[list[Any], int]:
    merged: list[Any] = []
    seen: set[str] = set()
    ids: dict[str, str] = {}
    dropped = 0
    for part_no, items in values:
        for item in items:
            fingerprint = canonical_json(item)
            if fingerprint in seen:
                dropped += 1
                continue
            seen.add(fingerprint)
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ident = item["id"]
                if ident in ids and ids[ident] != fingerprint:
                    new_id = f"{ident}-p{part_no}"
                    suffix = 2
                    while new_id in ids:
                        new_id = f"{ident}-p{part_no}-{suffix}"
                        suffix += 1
                    item = {**item, "id": new_id}
                    renamed.append({"field": field, "from": ident, "to": new_id, "part": part_no})
                    ident = new_id
                ids[ident] = fingerprint
            merged.append(item)
    return merged, dropped


def _sum_counts(values: list[tuple[int, list[Any]]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    totals: dict[tuple[Any, ...], int] = {}
    order: list[tuple[Any, ...]] = []
    for _, items in values:
        for item in items:
            if not isinstance(item, dict):
                continue
            ident = tuple(item.get(key) for key in keys)
            if ident not in totals:
                totals[ident] = 0
                order.append(ident)
            totals[ident] += int(item.get("count") or 0)
    return [{**dict(zip(keys, ident)), "count": totals[ident]} for ident in order if totals[ident] > 0]


def _weighted_share(values: list[tuple[int, list[Any]]], weights: dict[int, float]) -> list[dict[str, Any]]:
    total_weight = sum(weights.get(part_no, 1.0) for part_no, _ in values) or 1.0
    shares: dict[str, float] = {}
    order: list[str] = []
    for part_no, items in values:
        for item in items:
            if not isinstance(item, dict) or not item.get("person_id"):
                continue
            person = item["person_id"]
            if person not in shares:
                shares[person] = 0.0
                order.append(person)
            shares[person] += float(item.get("share_pct") or 0) * weights.get(part_no, 1.0)
    result = []
    for person in order:
        share = round(shares[person] / total_weight, 1)
        result.append({"person_id": person, "share_pct": int(share) if share.is_integer() else share})
    return result


def _scalar(values: list[tuple[int, Any]]) -> tuple[Any, int | None, bool]:
    """Последнее непустое значение по порядку частей; флаг — значения различаются."""
    distinct = {canonical_json(value) for _, value in values}
    chosen, chosen_part = None, None
    for part_no, value in values:
        if value not in (None, "", [], {}):
            chosen, chosen_part = value, part_no
    if chosen is None and values:
        chosen, chosen_part = values[-1][1], values[-1][0]
    return chosen, chosen_part, len(distinct) > 1


def merge_artifact(stem: str, parts: list[tuple[int, dict[str, Any]]], meeting_id: str, plan: dict[str, Any] | None) -> dict[str, Any]:
    part_numbers = [part_no for part_no, _ in parts]
    weights = _weights(plan, part_numbers)
    keys: list[str] = []
    for _, data in parts:
        for key in data:
            if key not in keys:
                keys.append(key)
    merged: dict[str, Any] = {}
    review_fields: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    renamed: list[dict[str, Any]] = []
    dropped = 0
    for key in keys:
        if key == "meeting_id":
            merged[key] = meeting_id
            continue
        present = [(part_no, data[key]) for part_no, data in parts if key in data]
        if any(isinstance(value, list) for _, value in present):
            lists = [(part_no, value) for part_no, value in present if isinstance(value, list)]
            if (stem, key) in KEYED_ENTITY_LISTS:
                merged[key] = _merge_keyed(stem, key, KEYED_ENTITY_LISTS[(stem, key)], lists, conflicts)
            elif stem == "05_power" and key == "question_marks_in_transcript":
                merged[key] = _sum_counts(lists, ("person_id",))
            elif stem == "05_power" and key == "interruptions":
                merged[key] = _sum_counts(lists, ("who", "whom"))
            elif stem == "05_power" and key == "talk_time_share":
                merged[key] = _weighted_share(lists, weights)
            else:
                merged[key], removed = _merge_list(key, lists, renamed)
                dropped += removed
            continue
        if (stem, key) in JOIN_TEXT_FIELDS:
            texts: list[str] = []
            for _, value in present:
                if isinstance(value, str) and value.strip() and value.strip() not in texts:
                    texts.append(value.strip())
            merged[key] = " ".join(texts) if texts else (present[-1][1] if present else None)
            continue
        value, chosen_part, differs = _scalar(present)
        merged[key] = value
        if differs or key in WHOLE_MEETING_FIELDS.get(stem, []):
            review_fields.append({"field": key, "chosen_from_part": chosen_part, "values_by_part": {str(part_no): item for part_no, item in present}, "differs": differs})

    if stem == "05_power":
        shares = merged.get("talk_time_share") or []
        if shares:
            leader = max(shares, key=lambda item: item["share_pct"])["person_id"]
            merged["dominant_speaker"] = leader
            for entry in review_fields:
                if entry["field"] == "dominant_speaker":
                    entry.update({"chosen_from_part": None, "note": "наибольшая доля речи после сведения"})
        note = f"Сведено из {len(parts)} частей: доли речи взвешены по объёму частей (знаки), вопросы и перебивания просуммированы."
        merged["evidence_note"] = f"{merged.get('evidence_note') or ''} {note}".strip()
    if stem == "06_emotions":
        points = [point for point in merged.get("sentiment_points") or [] if isinstance(point, dict)]
        summary = merged.get("overall_summary") if isinstance(merged.get("overall_summary"), dict) else None
        if summary is not None and points:
            negative = min(points, key=lambda point: (point.get("valence", 0), _seconds(point.get("timestamp_start")) or 0))
            tense = max(points, key=lambda point: (point.get("arousal", 0), -(_seconds(point.get("timestamp_start")) or 0)))
            merged["overall_summary"] = {**summary, "most_negative_timestamp": negative.get("timestamp_start"), "most_tense_timestamp": tense.get("timestamp_start")}
    for field in WHOLE_MEETING_FIELDS.get(stem, []):
        if "." in field and not any(entry["field"] == field for entry in review_fields):
            top, inner = field.split(".", 1)
            values = {str(part_no): (data.get(top) or {}).get(inner) if isinstance(data.get(top), dict) else None for part_no, data in parts}
            review_fields.append({"field": field, "chosen_from_part": parts[-1][0], "values_by_part": values, "differs": len({canonical_json(v) for v in values.values()}) > 1})
    return {"data": merged, "review_fields": review_fields, "conflicts": conflicts, "renamed_ids": renamed, "dropped_duplicates": dropped}


def _artifact_filter(value: str | None) -> set[str] | None:
    if not value:
        return None
    wanted: set[str] = set()
    for token in re.split(r"[,\s]+", value.strip()):
        if not token:
            continue
        token = token[:-5] if token.endswith(".json") else token
        matches = [name[:-5] for name in ANALYSIS_FILES if name[:-5] == token or name.startswith(f"{token}_")]
        if not matches:
            raise NaError(f"Неизвестный артефакт {token!r}: ожидается номер 01–11 или имя вида 07_commitments")
        wanted.update(matches)
    return wanted


def run_merge(root: Path, meeting_id: str, artifacts: str | None = None, force: bool = False) -> dict[str, Any]:
    from .schema_runtime import validate_contract
    from .validate import load_contracts

    path = meeting_dir(root, meeting_id)
    text = _transcript(path)
    plan, warnings = _load_plan(path, text)
    wanted = _artifact_filter(artifacts)
    grouped: dict[str, list[tuple[int, Path]]] = {}
    for file_path, stem, part_no in _part_files(path):
        if wanted is None or stem in wanted:
            grouped.setdefault(stem, []).append((part_no, file_path))
    if not grouped:
        raise NaError(f"Нет файлов частей для слияния в negotiations/{meeting_id}/{PARTS_DIR}/")
    registry = load_contracts()
    merged_report: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for stem in sorted(grouped):
        target = path / "analysis" / f"{stem}.json"
        if target.exists() and not force:
            skipped.append({"artifact": f"{stem}.json", "reason": "итоговый файл уже есть; --force перезапишет его сведением частей"})
            continue
        parts: list[tuple[int, dict[str, Any]]] = []
        broken: list[str] = []
        for part_no, file_path in sorted(grouped[stem]):
            data = read_json_or_none(file_path)
            if not isinstance(data, dict):
                broken.append(file_path.name)
                continue
            parts.append((part_no, data))
        if broken:
            skipped.append({"artifact": f"{stem}.json", "reason": "некорректный JSON в частях: " + ", ".join(broken)})
            continue
        numbers = [part_no for part_no, _ in parts]
        missing = [entry["part"] for entry in (plan or {}).get("parts", []) if entry.get("part") not in numbers]
        result = merge_artifact(stem, parts, meeting_id, plan)
        schema_errors = [f"{item['path']} [{item['keyword']}] {item['message']}" for item in validate_contract(result["data"], registry, registry["x-file-contracts"][f"{stem}.json"])]
        write_json(target, result["data"])
        merged_report.append(
            {
                "artifact": f"{stem}.json",
                "parts": numbers,
                "missing_parts": missing,
                "items": {key: len(value) for key, value in result["data"].items() if isinstance(value, list)},
                "dropped_duplicates": result["dropped_duplicates"],
                "renamed_ids": result["renamed_ids"],
                "conflicts": result["conflicts"],
                "review_fields": result["review_fields"],
                "schema_errors": schema_errors,
            }
        )
    valid = not skipped and all(not item["schema_errors"] for item in merged_report)
    return {"meeting_id": meeting_id, "valid": valid, "merged": merged_report, "skipped": skipped, "warnings": warnings}


def merge_lines(result: dict[str, Any]) -> list[str]:
    lines = [f"Слияние частей {result['meeting_id']}: сведено {len(result['merged'])}, пропущено {len(result['skipped'])}."]
    lines += [f"  ! {warning}" for warning in result["warnings"]]
    for item in result["merged"]:
        counts = ", ".join(f"{key} {value}" for key, value in item["items"].items())
        lines.append(f"  {item['artifact']} ← части {', '.join(map(str, item['parts']))}: {counts}" + (f"; снято точных дубликатов {item['dropped_duplicates']}" if item["dropped_duplicates"] else ""))
        if item["missing_parts"]:
            lines.append(f"    · нет частей: {', '.join(map(str, item['missing_parts']))}")
        for entry in item["renamed_ids"]:
            lines.append(f"    · id {entry['from']} → {entry['to']} ({entry['field']}, часть {entry['part']})")
        for entry in item["conflicts"]:
            lines.append(f"    · расхождение {entry['field']} {entry['key']} (части {entry['parts']}), оставлено из части {entry['kept_from_part']}")
        for entry in item["review_fields"]:
            source = entry.get("note") or f"черновик из части {entry['chosen_from_part']}"
            lines.append(f"    ? {entry['field']}: {source}" + (" (части расходятся)" if entry["differs"] else "") + " — утвердить по всей встрече")
        for error in item["schema_errors"][:10]:
            lines.append(f"    - контракт: {error}")
    for entry in result["skipped"]:
        lines.append(f"  пропущено {entry['artifact']}: {entry['reason']}")
    return lines
