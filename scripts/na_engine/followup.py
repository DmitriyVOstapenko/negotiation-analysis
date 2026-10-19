"""Follow-up пакет: структурированный JSON, протокол и черновик сообщения (всегда draft_not_sent)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis_profile import meeting_profile
from .common import NaError, meeting_dir, now_iso, read_json, read_json_or_none, relative, write_json, write_text
from .registries import meeting_commitments, meeting_risks
from .report import cell, evidence_text, person


DELIVERY_STATUS = "draft_not_sent"


def row(values: list[Any]) -> str:
    return "| " + " | ".join(cell(value) for value in values) + " |"


def build_bundle(root: Path, meeting_path: Path) -> dict[str, Any]:
    meeting_id = meeting_path.name
    analysis = meeting_path / "analysis"
    profile = meeting_profile(meeting_path)
    # лёгкий разбор: карта решений (09) и протокол следующей встречи (11) не строятся
    needed = ("01_extraction.json", "07_commitments.json") + (("09_decision_authority.json", "11_meeting_effectiveness.json") if profile == "full" else ())
    for name in needed:
        if not (analysis / name).is_file():
            raise NaError(f"Нет аналитического файла {name}; follow-up собирается после анализа")
    extraction = read_json(analysis / "01_extraction.json")
    decisions = read_json(analysis / "09_decision_authority.json") if profile == "full" else {}
    commitments_analysis = read_json(analysis / "07_commitments.json")
    effectiveness = read_json(analysis / "11_meeting_effectiveness.json") if profile == "full" else {}
    queue = read_json_or_none(root / "system" / "review_queue.json")
    if queue is None:
        from .review import build_queue

        queue = build_queue(root)
    review_items = [item for item in queue.get("items", []) if item.get("meeting_id") == meeting_id and item.get("status") == "open"]
    return {
        "follow_up_version": "1.0.0",
        "generated_at": now_iso(),
        "delivery_status": DELIVERY_STATUS,
        "meeting_id": meeting_id,
        "analysis_profile": profile,
        "outcome": extraction.get("outcome"),
        "outcome_summary": extraction.get("outcome_summary"),
        "participants": extraction.get("participants", []),
        "decision_areas": decisions.get("decision_areas", []),
        "unresolved_decisions": decisions.get("unresolved", []),
        "commitments": meeting_commitments(root, meeting_path),
        "open_questions": commitments_analysis.get("open_questions", []),
        "risks": meeting_risks(root, meeting_path),
        "review_items": review_items,
        "next_meeting_protocol": effectiveness.get("next_meeting_protocol", []),
    }


def build_protocol(bundle: dict[str, Any]) -> str:
    names = {item["person_id"]: item["full_name"] for item in bundle["participants"]}
    lines = [
        "# Протокол по итогам встречи",
        "",
        f"**Meeting ID:** `{bundle['meeting_id']}`  ",
        f"**Статус документа:** `{DELIVERY_STATUS}` — черновик, собран автоматически из проверенных данных; не отправлен.",
        "",
        "## Итог",
        "",
        bundle["outcome_summary"] or "Итог не сформирован.",
        "",
        "## Решения и состояние",
        "",
        "| Вопрос | Текущее состояние | Финальный ЛПР | Владелец следующего шага | Срок | Основание |",
        "|---|---|---|---|---|---|",
    ]
    for decision in bundle["decision_areas"]:
        lines.append(row([
            decision["decision"], decision["current_state"], person(decision.get("final_decision_maker"), names),
            person(decision.get("proposal_owner"), names), decision.get("deadline"), evidence_text(decision.get("evidence", [])),
        ]))
    if not bundle["decision_areas"]:
        light = bundle.get("analysis_profile") == "light"
        lines.append(row(["—", "Карта решений не строилась (лёгкий разбор)" if light else "Решения не выделены", "—", "—", "—", "—"]))

    lines += ["", "## Действия", "", "| Статус | Ответственный | Действие | Срок | Источник |", "|---|---|---|---|---|"]
    for commitment in bundle["commitments"]:
        lines.append(row([
            commitment.get("status"), person(commitment.get("who"), names), commitment.get("what"), commitment.get("deadline"),
            f"«{commitment.get('source_quote')}» (`{commitment.get('source_timestamp')}`)",
        ]))
    if not bundle["commitments"]:
        lines.append(row(["—", "—", "Обязательства не зафиксированы", "—", "—"]))

    lines += ["", "## Открытые вопросы", ""]
    if bundle["open_questions"]:
        for question in bundle["open_questions"]:
            lines.append(f"- {question['question']} — «{question['quote']}» (`{question['timestamp']}`).")
    else:
        lines.append("- Открытые вопросы из транскрипта не выделены.")
    if bundle["unresolved_decisions"]:
        lines += ["", "Незакрытые элементы карты полномочий:", ""]
        lines.extend(f"- {value}" for value in bundle["unresolved_decisions"])

    lines += ["", "## Риски", "", "| Статус | Важность | Риск | Владелец | Срок | Действие |", "|---|---|---|---|---|---|"]
    for risk in bundle["risks"]:
        mitigation = risk.get("mitigation") or {}
        lines.append(row([risk.get("status"), risk.get("severity"), risk.get("description"), person(mitigation.get("owner"), names), mitigation.get("deadline"), mitigation.get("planned_actions")]))
    if not bundle["risks"]:
        lines.append(row(["—", "—", "Риски не выделены", "—", "—", "—"]))

    lines += ["", "## Требует подтверждения человека", ""]
    if bundle["review_items"]:
        lines.extend(f"- `{entry['item_id']}` — {entry['question']}" for entry in bundle["review_items"])
    else:
        lines.append("- Открытых пунктов очереди проверки по этой встрече нет.")
    lines += ["", "## Правило следующей встречи", ""]
    lines.extend(f"{index}. {value}" for index, value in enumerate(bundle["next_meeting_protocol"], 1))
    if not bundle["next_meeting_protocol"] and bundle.get("analysis_profile") == "light":
        lines.append("- Не строилось: лёгкий разбор (только обязательства и риски).")
    lines += ["", f"_Документ подготовлен, но никуда не отправлен (`{DELIVERY_STATUS}`)._", ""]
    return "\n".join(lines)


def build_message(bundle: dict[str, Any]) -> str:
    names = {item["person_id"]: item["full_name"] for item in bundle["participants"]}
    status_labels = {"open": "открыто", "in_progress": "в работе", "overdue": "просрочено", "done": "выполнено", "closed": "закрыто", "agreed": "согласовано", "blocked": "заблокировано"}
    lines = [
        "# Черновик follow-up сообщения",
        "",
        f"_Статус: `{DELIVERY_STATUS}`. Не отправлено. Адресаты не определены автоматически._",
        "",
        "Коллеги, фиксирую итоги встречи.",
        "",
        bundle["outcome_summary"] or "",
    ]
    if bundle["decision_areas"]:
        lines += ["", "По решениям:"]
        for decision in bundle["decision_areas"]:
            lines.append(f"- {decision['decision']}: {decision['current_state']}. Следующий шаг — {person(decision.get('proposal_owner'), names)}, срок {decision.get('deadline') or 'не определён'}.")
    if bundle["commitments"]:
        lines += ["", "По действиям:"]
        for commitment in bundle["commitments"]:
            status = status_labels.get(commitment.get("status"), commitment.get("status"))
            deadline_clause = f"срок {commitment.get('deadline')}" if commitment.get("deadline") else "срок не определён"
            lines.append(f"- {person(commitment.get('who'), names)} — {commitment.get('what')}; {deadline_clause}; статус: {status}.")
    open_risks = [risk for risk in bundle["risks"] if risk.get("status") != "closed"]
    if open_risks:
        lines += ["", "Риски, которые остаются активными:"]
        for risk in open_risks:
            mitigation = risk.get("mitigation") or {}
            lines.append(f"- {risk['description']} Владелец: {person(mitigation.get('owner'), names)}; срок: {mitigation.get('deadline') or 'не определён'}.")
    if bundle["review_items"]:
        lines += ["", "Нужно отдельно подтвердить:"]
        lines.extend(f"- {entry['question']}" for entry in bundle["review_items"])
    lines += ["", "Если формулировки отражены неверно, прошу указать конкретный пункт для исправления.", ""]
    return "\n".join(lines)


def run_followup(root: Path, meeting_id: str) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    bundle = build_bundle(root, path)
    outputs = {
        "follow_up.json": path / "follow_up.json",
        "follow_up_protocol.md": path / "follow_up_protocol.md",
        "follow_up_message.md": path / "follow_up_message.md",
    }
    write_json(outputs["follow_up.json"], bundle)
    write_text(outputs["follow_up_protocol.md"], build_protocol(bundle))
    write_text(outputs["follow_up_message.md"], build_message(bundle))
    return {
        "valid": True,
        "meeting_id": meeting_id,
        "delivery_status": DELIVERY_STATUS,
        "outputs": [relative(item, root) for item in outputs.values()],
    }
