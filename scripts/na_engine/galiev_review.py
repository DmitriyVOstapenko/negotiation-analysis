"""Evidence and inclusion checks for the default full-analysis methodology chapter.

These checks establish references and completeness, not the quality of the model's
interpretations. The original eleven analysis contracts remain independent.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .analysis_profile import meeting_profile
from .common import PLUGIN_ROOT, NaError, read_json_or_none, read_yaml, sha256_file
from .quote_check import check_quotes, collect_quotes, to_seconds
from .source import parse_segments


REVIEW_FILE = "12_galiev_review.json"
HEADING = "## Разбор по методике Арти Галиева"
DATA_ROOT = PLUGIN_ROOT / "skills" / "negociar-ru" / "data"
CANON_FILES = tuple(sorted(path for path in (DATA_ROOT / "canon").glob("[0-9][0-9]-*.md") if not path.name.startswith("00-")))
CORPUS_FILES = (*CANON_FILES, DATA_ROOT / "casos.json")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@lru_cache(maxsize=2)
def _cases(digest: str | None) -> dict[int, dict[str, Any]]:
    # Cache only while the actual library content is unchanged.
    data = json.loads((DATA_ROOT / "casos.json").read_text(encoding="utf-8-sig"))
    return {number: item for number, item in enumerate(data, start=1)}


def check_review(meeting_path: Path) -> dict[str, Any]:
    try:
        review = read_yaml(meeting_path / "review.yaml")
    except NaError:
        review = {}
    policy = review.get("galiev_review")
    required = meeting_profile(meeting_path) == "full" and isinstance(policy, dict) and policy.get("required") is True
    result: dict[str, Any] = {"required": required, "valid": True, "errors": [], "warnings": [], "file": REVIEW_FILE}
    if not required:
        return result
    errors = result["errors"]
    if policy.get("policy_version") != 1:
        errors.append("неподдерживаемая версия galiev_review.policy_version; требуется 1")
    data = read_json_or_none(meeting_path / "analysis" / REVIEW_FILE)
    if not isinstance(data, dict):
        errors.append(f"{REVIEW_FILE}: отсутствует или не является объектом JSON")
        result["valid"] = False
        return result
    result["data"] = data
    if data.get("meeting_id") != meeting_path.name:
        errors.append("meeting_id методического разбора не совпадает со встречей")
    if data.get("method") != "negociar-ru":
        errors.append("method должен быть negociar-ru")
    transcript_digest = sha256_file(meeting_path / "transcript.txt")
    if not transcript_digest or data.get("transcript_sha256") != transcript_digest:
        errors.append("transcript_sha256 методического разбора устарел или не совпадает")
    if not _text(data.get("summary")):
        errors.append("summary методического разбора должен быть заполнен")
    for field in ("principles", "cases", "participants", "limitations"):
        if not isinstance(data.get(field), list):
            errors.append(f"{field} должен быть массивом")
    if errors:
        result["valid"] = False
        return result
    if any(not _text(item) for item in data["limitations"]):
        errors.append("limitations должен содержать непустые строки")
    principles = data["principles"]
    if not principles and (not data["limitations"] or any(isinstance(p, dict) and p.get("status") == "assessed" for p in data["participants"])):
        errors.append("без применимого принципа допустим только явный отказ от оценки всех участников с общим ограничением")
    canonical = {path.name: path for path in CANON_FILES}
    for index, item in enumerate(principles):
        prefix = f"principles[{index}]"
        if not isinstance(item, dict) or not all(_text(item.get(key)) for key in ("file", "heading", "source_quote")):
            errors.append(f"{prefix}: нужны file, heading и source_quote")
            continue
        path = canonical.get(item["file"])
        if path is None:
            errors.append(f"{prefix}: file не является файлом канона")
            continue
        content = path.read_text(encoding="utf-8-sig")
        heading = re.search(r"^### " + re.escape(item["heading"]) + r"\s*$", content, re.MULTILINE)
        if not heading:
            errors.append(f"{prefix}: heading не найден в указанном файле канона")
            continue
        end = re.search(r"^#{1,3} ", content[heading.end():], re.MULTILINE)
        section = content[heading.end():heading.end() + end.start()] if end else content[heading.end():]
        if item["source_quote"] not in section:
            errors.append(f"{prefix}: source_quote не найдена внутри указанного принципа")
    search = data.get("case_search")
    if not isinstance(search, dict) or not isinstance(search.get("queries"), list) or not search["queries"] or any(not _text(q) for q in search["queries"]):
        errors.append("case_search.queries: нужны реальные поисковые запросы к библиотеке")
        search = {}
    try:
        library = _cases(sha256_file(DATA_ROOT / "casos.json"))
    except (OSError, ValueError, KeyError, TypeError):
        library = {}
        errors.append("библиотека случаев недоступна или повреждена")
    numbers: set[int] = set()
    for index, item in enumerate(data["cases"]):
        prefix = f"cases[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: нужен объект случая")
            continue
        number = item.get("number")
        actual = library.get(number) if type(number) is int else None
        if not actual or actual.get("id") != item.get("id") or actual.get("title") != item.get("title"):
            errors.append(f"{prefix}: number, id и title не совпадают с реальным случаем библиотеки")
        if type(number) is int:
            if number in numbers:
                errors.append(f"{prefix}: повторный номер случая")
            numbers.add(number)
        if not _text(item.get("relevance")) or not _text(item.get("difference")):
            errors.append(f"{prefix}: нужны применимость relevance и различие difference")
    if not data["cases"] and not _text(search.get("no_match_reason")):
        errors.append("без подходящих случаев нужен case_search.no_match_reason")
    if data["cases"] and search.get("no_match_reason") is not None:
        errors.append("при найденных случаях case_search.no_match_reason должен быть null")
    extraction = read_json_or_none(meeting_path / "analysis" / "01_extraction.json")
    source_participants = extraction.get("participants", []) if isinstance(extraction, dict) else []
    expected = {p.get("person_id") for p in source_participants if isinstance(p, dict) and _text(p.get("person_id"))}
    if not expected:
        errors.append("не найдены участники в 01_extraction.json")
    seen: set[str] = set()
    transcript_path = meeting_path / "transcript.txt"
    transcript = transcript_path.read_text(encoding="utf-8-sig") if transcript_path.is_file() else ""
    timed = any(segment.get("timestamp") for segment in parse_segments(transcript))
    evidence_items = []
    for index, participant in enumerate(data["participants"]):
        prefix = f"participants[{index}]"
        if not isinstance(participant, dict):
            errors.append(f"{prefix}: нужен объект участника")
            continue
        pid = participant.get("participant_id")
        if not isinstance(pid, str) or pid not in expected or pid in seen:
            errors.append(f"{prefix}: participant_id неизвестен или повторяется")
        elif pid:
            seen.add(pid)
        status = participant.get("status")
        if status not in ("assessed", "insufficient_evidence"):
            errors.append(f"{prefix}: неизвестный status")
        for field in ("evidence", "principle_refs", "case_refs", "next_steps", "limitations"):
            if not isinstance(participant.get(field), list):
                errors.append(f"{prefix}.{field}: нужен массив")
        if any(not isinstance(participant.get(field), list) for field in ("evidence", "principle_refs", "case_refs", "next_steps", "limitations")):
            continue
        if any(not _text(item) for item in participant["limitations"]):
            errors.append(f"{prefix}.limitations: нужны непустые строки")
        if status == "assessed":
            for field in ("assessment", "alternative"):
                if not _text(participant.get(field)):
                    errors.append(f"{prefix}.{field}: нужна интерпретация и альтернативное объяснение")
            for field in ("evidence", "principle_refs", "next_steps"):
                if not participant[field]:
                    errors.append(f"{prefix}.{field}: нужен хотя бы один элемент")
        elif status == "insufficient_evidence":
            if not participant["limitations"]:
                errors.append(f"{prefix}: объясните недостаток данных в limitations")
            if participant.get("assessment") or participant.get("alternative") or participant["next_steps"]:
                errors.append(f"{prefix}: при insufficient_evidence нельзя добавлять оценку или рекомендации")
        for ref in participant["principle_refs"]:
            if type(ref) is not int or not 0 <= ref < len(principles):
                errors.append(f"{prefix}: неизвестный индекс principle_refs")
        for ref in participant["case_refs"]:
            if type(ref) is not int or ref not in numbers:
                errors.append(f"{prefix}: неизвестный номер case_refs")
        for step in participant["next_steps"]:
            if not isinstance(step, dict) or not _text(step.get("action")) or not _text(step.get("tradeoff")):
                errors.append(f"{prefix}.next_steps: нужны action и цена/риск tradeoff")
        for evidence in participant["evidence"]:
            if not isinstance(evidence, dict) or not _text(evidence.get("quote")):
                errors.append(f"{prefix}.evidence: нужна цитата quote")
                continue
            if timed and to_seconds(evidence.get("timestamp")) is None:
                errors.append(f"{prefix}.evidence: нужен корректный timestamp")
            evidence_items.extend(collect_quotes(REVIEW_FILE, {**evidence, "speaker": pid}))
    if seen != expected:
        errors.append("не охвачены участники 01_extraction.json: " + ", ".join(sorted(expected - seen)))
    quote_result = check_quotes(transcript, evidence_items, review, source_participants)
    errors.extend(quote_result["errors"])
    result["warnings"] = quote_result["warnings"]
    result["checked_quotes"] = len(evidence_items)
    result["valid"] = not errors
    return result


def render_review(data: dict[str, Any], names: dict[str, str] | None = None) -> str:
    names = names or {}
    lines = [HEADING, "", "Интерпретации по канону отделены от фактов встречи. Это оценка поведения в этой встрече, а не свойств личности.", "", data["summary"], "", "### Основания методики"]
    for index, principle in enumerate(data["principles"]):
        lines += ["", f"Принцип [{index}]: **{principle['heading']}** ({principle['file']})", "", f"> {principle['source_quote']}"]
    for case in data["cases"]:
        lines += ["", f"Случай №{case['number']}: **{case['title']}** (id {case['id']})", f"Применимость: {case['relevance']}", f"Различие: {case['difference']}"]
    lines += ["", "Поиск по библиотеке: " + "; ".join(data["case_search"]["queries"])]
    if not data["cases"]:
        lines += ["", "Подходящий случай не найден: " + data["case_search"]["no_match_reason"]]
    for participant in data["participants"]:
        pid = participant["participant_id"]
        lines += ["", "### " + names.get(pid, pid)]
        if participant["status"] == "insufficient_evidence":
            lines += ["", "Недостаточно данных для оценки."]
        else:
            lines += ["", "**Фактические опоры:**"]
            for evidence in participant["evidence"]:
                lines += ["", f"> {evidence['quote']}", f"Таймкод: {evidence.get('timestamp') or 'нет в источнике'}"]
            lines += ["", "**Интерпретация:** " + participant["assessment"], "", "**Альтернативное объяснение:** " + participant["alternative"], "", "Принципы: " + ", ".join(f"[{ref}]" for ref in participant["principle_refs"])]
            if participant["case_refs"]:
                lines += ["Случаи библиотеки: " + ", ".join(f"№{ref}" for ref in participant["case_refs"])]
            lines += ["", "**Рекомендации и их цена:**", ""]
            lines += [f"- {step['action']} Цена/риск: {step['tradeoff']}" for step in participant["next_steps"]]
        if participant["limitations"]:
            lines += ["", "Ограничения: " + "; ".join(participant["limitations"])]
    if data["limitations"]:
        lines += ["", "### Ограничения разбора", "", *[f"- {item}" for item in data["limitations"]]]
    return "\n".join(lines) + "\n"


def report_errors(meeting_path: Path, text: str) -> list[str]:
    """Check retained content in final/automatic report; no claim about reasoning quality."""
    checked = check_review(meeting_path)
    if not checked["required"]:
        return []
    if not checked["valid"]:
        return ["методический разбор не прошёл проверку: " + error for error in checked["errors"]]
    data = checked["data"]
    chapter = re.search(r"^" + re.escape(HEADING) + r"\s*$", text, re.MULTILINE)
    if not chapter:
        return ["в отчёте отсутствует раздел: " + HEADING]
    end = re.search(r"^## ", text[chapter.end():], re.MULTILINE)
    content = text[chapter.end():chapter.end() + end.start()] if end else text[chapter.end():]
    required = [data["summary"], *data["limitations"]]
    required += [item["heading"] for item in data["principles"]]
    required += [value for item in data["cases"] for value in (item["title"], item["relevance"], item["difference"])]
    if not data["cases"]:
        required.append(data["case_search"]["no_match_reason"])
    for participant in data["participants"]:
        if participant["status"] == "assessed":
            required += [participant["assessment"], participant["alternative"]]
            required += [evidence["quote"] for evidence in participant["evidence"]]
            required += [value for step in participant["next_steps"] for value in (step["action"], step["tradeoff"])]
        required += participant["limitations"]
    return ["в разделе отсутствует часть разбора по методике Арти Галиева: " + value[:100] for value in required if value not in content]
