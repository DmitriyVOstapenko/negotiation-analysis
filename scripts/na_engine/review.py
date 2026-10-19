"""Единая очередь вопросов для человека и запись подтверждённых решений."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .common import (
    NaError,
    append_jsonl,
    gate_state,
    meeting_dirs,
    now_iso,
    read_json_or_none,
    read_jsonl,
    relative,
    walk_dicts,
    write_json,
    write_text,
)


DECISIONS_RELATIVE = "knowledge/review_decisions.jsonl"
CLOSED_STATUSES = {"closed", "resolved", "dismissed"}
VALID_RECORD_STATUSES = ["closed", "deferred"]


def stable_id(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return "rq-" + hashlib.sha256(raw).hexdigest()[:16]


def item(category: str, meeting_id: str | None, question: str, source_path: str, source_ref: str, priority: str = "normal", evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "item_id": stable_id(category, meeting_id or "project", source_path, source_ref, question),
        "status": "open",
        "category": category,
        "priority": priority,
        "meeting_id": meeting_id,
        "question": question,
        "source": "derived_from_canonical_data",
        "source_path": source_path,
        "source_ref": source_ref,
        "evidence": evidence,
        "answer": None,
        "resolved_at": None,
    }


def latest_decisions(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for decision in read_jsonl(root / DECISIONS_RELATIVE):
        if decision.get("item_id"):
            result[decision["item_id"]] = decision
    return result


def build_queue(root: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for meeting_path in meeting_dirs(root):
        meeting_id = meeting_path.name
        gate = gate_state(meeting_path)
        review = gate["review"]
        review_rel = relative(meeting_path / "review.yaml", root)
        for index, question in enumerate(review.get("open_review_items") or []):
            if isinstance(question, dict):
                question = question.get("question") or json.dumps(question, ensure_ascii=False)
            items.append(item("meeting_review", meeting_id, str(question), review_rel, f"open_review_items[{index}]", priority="high"))
        if gate["consent"] == "not_set":
            items.append(item("privacy_gate", meeting_id, "Пройти шлюз согласия: consent, маскирование, чувствительность, маршрут", review_rel, "processing_gate", priority="critical"))
        speakers = review.get("speaker_mapping") if isinstance(review.get("speaker_mapping"), dict) else {}
        if speakers.get("status") != "confirmed":
            items.append(item("speaker_mapping", meeting_id, "Подтвердить или исправить карту спикеров", review_rel, "speaker_mapping", priority="high"))

        extraction_path = meeting_path / "analysis" / "01_extraction.json"
        extraction = read_json_or_none(extraction_path) or {}
        for index, participant in enumerate(extraction.get("participants", [])):
            full_name = (participant or {}).get("full_name") or ""
            if re.fullmatch(r"(?:Speaker|Спикер)\s*\d+", full_name, re.IGNORECASE):
                items.append(item("unidentified_speaker", meeting_id, f"Установить личность {full_name}", relative(extraction_path, root), f"$.participants[{index}]", priority="high"))

        analysis_dir = meeting_path / "analysis"
        if analysis_dir.is_dir():
            for analysis_path in sorted(analysis_dir.glob("[0-1][0-9]_*.json")):
                data = read_json_or_none(analysis_path)
                if not isinstance(data, dict):
                    continue
                for node, json_path in walk_dicts(data):
                    confidence = node.get("confidence")
                    if confidence not in {"possible", "speculative"}:
                        continue
                    summary = next((node.get(key) for key in ("fact", "description", "claim", "decision", "what", "explanation", "title", "position") if node.get(key)), "вывод без краткой формулировки")
                    quote = node.get("quote") or node.get("source_quote")
                    timestamp = node.get("timestamp") or node.get("source_timestamp")
                    items.append(item("low_confidence_finding", meeting_id, f"Проверить вывод с confidence={confidence}: {summary}", relative(analysis_path, root), json_path, priority="normal", evidence={"quote": quote, "timestamp": timestamp} if quote else None))

    for commitment in read_jsonl(root / "knowledge/commitments/open.jsonl"):
        if not commitment.get("deadline"):
            items.append(item("commitment_deadline", commitment.get("meeting_id"), f"Назначить срок: {commitment.get('what')}", "knowledge/commitments/open.jsonl", str(commitment.get("id")), priority="high" if commitment.get("status") == "overdue" else "normal", evidence={"quote": commitment.get("source_quote"), "timestamp": commitment.get("source_timestamp")}))
    for risk in read_jsonl(root / "knowledge/risks/active.jsonl"):
        mitigation = risk.get("mitigation") or {}
        priority = "high" if risk.get("severity") == "high" else "normal"
        if not mitigation.get("owner"):
            items.append(item("risk_owner", risk.get("last_seen_meeting_id"), f"Назначить владельца риска: {risk.get('description')}", "knowledge/risks/active.jsonl", str(risk.get("id")), priority=priority))
        if not mitigation.get("deadline"):
            items.append(item("risk_deadline", risk.get("last_seen_meeting_id"), f"Назначить срок митигации риска: {risk.get('description')}", "knowledge/risks/active.jsonl", str(risk.get("id")), priority=priority))

    try:  # зависшие вопросы: тема без решения, повторный перенос срока, обещание без результата
        from .stalled import queue_items as stalled_queue_items

        items.extend(stalled_queue_items(root, item))
    except NaError:
        pass  # повреждённый реестр или журнал показывает контрольный центр; очередь строится без этих пунктов

    deduplicated = {entry["item_id"]: entry for entry in items}
    decisions = latest_decisions(root)
    for entry in deduplicated.values():
        decision = decisions.get(entry["item_id"])
        if decision:
            status = decision.get("status")
            entry["status"] = "closed" if status in CLOSED_STATUSES else status
            entry["answer"] = decision.get("answer")
            entry["resolved_at"] = decision.get("recorded_at")
            entry["decision_source"] = decision.get("source")

    priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    ordered = sorted(deduplicated.values(), key=lambda entry: (entry["status"] != "open", priority_order.get(entry["priority"], 9), entry.get("meeting_id") or "", entry["item_id"]))
    counts: dict[str, int] = {}
    for entry in ordered:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "review_queue_version": "1.1.0",
        "generated_at": now_iso(),
        "summary": {"total": len(ordered), "open": counts.get("open", 0), "closed": counts.get("closed", 0), "deferred": counts.get("deferred", 0)},
        "items": ordered,
    }


def markdown(queue: dict[str, Any]) -> str:
    summary = queue["summary"]
    lines = [
        "# Единая очередь проверки",
        "",
        f"Открыто: **{summary['open']}** · всего: {summary['total']} · закрыто: {summary['closed']} · отложено: {summary['deferred']}",
        "",
        "| ID | Приоритет | Категория | Встреча | Вопрос | Статус |",
        "|---|---|---|---|---|---|",
    ]
    for entry in queue["items"]:
        values = [entry["item_id"], entry["priority"], entry["category"], entry.get("meeting_id") or "—", entry["question"].replace("|", "\\|").replace("\n", " "), entry["status"]]
        lines.append("| " + " | ".join(values) + " |")
        if entry.get("answer"):
            lines.append(f"|  |  |  |  | Решение: {str(entry['answer']).replace('|', '\\|')} |  |")
    lines += ["", "Очередь — производное представление. Решения записываются только как подтверждения пользователя в `knowledge/review_decisions.jsonl`.", ""]
    return "\n".join(lines)


def run_queue(root: Path) -> dict[str, Any]:
    queue = build_queue(root)
    output_json = root / "system" / "review_queue.json"
    output_md = root / "system" / "review_queue.md"
    write_json(output_json, queue)
    write_text(output_md, markdown(queue))
    return {"valid": True, "summary": queue["summary"], "outputs": [relative(output_json, root), relative(output_md, root)]}


def record_decision(root: Path, item_id: str, decision_text: str, status: str = "closed") -> dict[str, Any]:
    if status not in VALID_RECORD_STATUSES:
        raise NaError(f"--status: допустимо {', '.join(VALID_RECORD_STATUSES)}")
    if not decision_text or not decision_text.strip():
        raise NaError("--decision не может быть пустым: решение фиксируется только с формулировкой пользователя")
    queue = build_queue(root)
    known = {entry["item_id"]: entry for entry in queue["items"]}
    if item_id not in known:
        raise NaError(f"Неизвестный пункт очереди: {item_id}. Выполните `na.py review queue` и возьмите актуальный ID.")
    recorded_at = now_iso()
    digest_source = f"{item_id}\x1f{status}\x1f{decision_text}\x1f{recorded_at}"
    event = {
        "decision_id": "rqd-" + hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:20],
        "item_id": item_id,
        "status": status,
        "answer": decision_text.strip(),
        "source": "user_confirmation",
        "recorded_at": recorded_at,
        "source_question": known[item_id]["question"],
        "category": known[item_id]["category"],
        "meeting_id": known[item_id].get("meeting_id"),
    }
    append_jsonl(root / DECISIONS_RELATIVE, [event])
    queue_result = run_queue(root)
    return {"valid": True, "decision": event, "queue": queue_result["summary"]}
