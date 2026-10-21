"""Follow-up пакет: структурированный JSON, протокол и черновик сообщения (всегда draft_not_sent).

Протокол — внутренний документ: решения, действия, вопросы, риски, очередь проверки. Сообщение —
черновик для другой стороны: принятые решения, действующие обязательства не ниже probable,
формулировки открытых решений и вопросов, следующий шаг; ни рисков, ни очереди проверки, ни id.

Подписи людей, сроки, стыки полей и «принято ли решение» — теми же хелперами, что report_auto.md
(report.py: person, decision_status): протокол и сообщение не расходятся с отчётом. Принятое — только
по статусному префиксу current_state («Принято: …»); без префикса решение открытое.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .analysis_profile import meeting_profile
from .common import NaError, meeting_dir, now_iso, read_json, read_json_or_none, relative, write_json, write_text
from .registries import meeting_commitments, meeting_risks
from .report import (
    cell, clause, commitment_label, deadline_text, decision_status, evidence_text, is_person_id, low_confidence,
    name_map, person, sentence, short_role,
)


DELIVERY_STATUS = "draft_not_sent"
UNCONFIRMED_MARK = " (требует подтверждения)"
# в сообщении другой стороне — только действующие обязательства
INACTIVE_COMMITMENT_STATUSES = {"done", "cancelled"}
# человек не из участников и без наблюдения в 09: в сообщении — без внутреннего id
UNNAMED_OUTSIDER = "не участник встречи, имя не названо"
# внутренняя пометка разбора в скобках: таймкод «(00:02:16)», id «(risk-…)», ссылка на пункт «(C1)»;
# id — латиница с двумя дефисами и больше или с цифрой («e-mail» в скобках остаётся)
_ID = r"(?=[a-z0-9-]*[a-z])(?:[a-z0-9]+(?:-[a-z0-9]+){2,}|(?=[a-z-]*\d)[a-z0-9]+(?:-[a-z0-9]+)+)"
_INTERNAL_NOTE = re.compile(rf"\s*\((?=[^()]*(?:\b\d{{1,2}}:\d{{2}}\b|\b{_ID}\b|\b[A-Z]{{1,2}}\d{{1,3}}\b))[^()]*\)")


def row(values: list[Any]) -> str:
    return "| " + " | ".join(cell(value) for value in values) + " |"


def current_commitment(item: dict[str, Any]) -> dict[str, Any]:
    """Обязательство для follow-up — только действующая формулировка: related_mentions (предложение,
    принятие, прежние версии) нужны отчёту, а не протоколу и сообщению другой стороне."""
    return {key: value for key, value in item.items() if key != "related_mentions"}


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
        # подписи людей вне встречи (ЛПР, исполнитель, владелец риска) — по наблюдаемой роли, как в отчёте
        "authority_observations": decisions.get("authority_observations", []),
        "decision_areas": decisions.get("decision_areas", []),
        "unresolved_decisions": decisions.get("unresolved", []),
        "commitments": [current_commitment(item) for item in meeting_commitments(root, meeting_path)],
        "open_questions": commitments_analysis.get("open_questions", []),
        "risks": meeting_risks(root, meeting_path),
        "review_items": review_items,
        "next_meeting_protocol": effectiveness.get("next_meeting_protocol", []),
    }


def bundle_names(bundle: dict[str, Any]) -> dict[str, str]:
    """person_id → подпись: участники — по имени; люди вне встречи — роль из 09 «(не участник встречи)»."""
    return name_map({"participants": bundle.get("participants") or []}, {"authority_observations": bundle.get("authority_observations") or []})


def action_text(commitment: dict[str, Any]) -> Any:
    """Действие для таблицы протокола; ниже probable — с пометкой «(требует подтверждения)», как в отчёте."""
    return f"{clause(commitment.get('what'))}{UNCONFIRMED_MARK}" if low_confidence(commitment) else commitment.get("what")


def split_decisions(bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(принятые, открытые) решения для сообщения — то же правило, что «Требуемое решение» отчёта.

    Принятое — только по статусному префиксу current_state («Принято: …», «Решено. …», «Согласовано — …»,
    «Утверждено: …») и с confidence не ниже probable. Всё остальное — открытое: без префикса (в том числе
    старые артефакты, «Договорённости нет: …», «Согласовано только с юристами…»), «Не решено: …»,
    «Отложено: …», а также «Принято: …» с confidence ниже probable — другой стороне не выдаётся за принятое."""
    taken: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for item in bundle["decision_areas"]:
        if not isinstance(item, dict):
            continue
        status, _ = decision_status(item.get("current_state"))
        (taken if status and not low_confidence(item) else pending).append(item)
    return taken, pending


def taken_text(decision: dict[str, Any], replacements: dict[str, str]) -> str:
    """Принятое решение для сообщения: «формулировка — принято: суть» (статус — словом, без «Принято: Принято:»)."""
    status, rest = decision_status(decision.get("current_state"))
    title = clause(public_text(decision.get("decision"), replacements))
    rest = sentence(public_text(rest, replacements))
    return f"{title} — {status}: {rest}" if rest else f"{title} — {status}."


def public_names(bundle: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Подписи для сообщения и замены id разбора в его тексте.

    Люди — как в протоколе (имя участника, роль из 09), но человек вне встречи без наблюдения в 09 —
    «не участник встречи, имя не названо», а не `id`. Свободный текст вместо id («не установлен: сторона …»)
    — как есть, как в протоколе (report.person). id рисков и обязательств из текста убираются.
    """
    names = bundle_names(bundle)
    mentioned: set[Any] = set()
    for item in bundle["commitments"]:
        mentioned |= {item.get("who"), item.get("to_whom")}
    for item in bundle["decision_areas"]:
        mentioned |= {item.get("final_decision_maker"), item.get("proposal_owner"), *(item.get("advisory_participants") or [])}
    for item in bundle["risks"]:
        mentioned.add((item.get("mitigation") or {}).get("owner"))
    people = set(names) | {pid for pid in mentioned if is_person_id(pid)}
    labels = {pid: names.get(pid, UNNAMED_OUTSIDER) for pid in people}
    things = {item.get("id") for item in [*bundle["commitments"], *bundle["risks"]]}
    replacements = {**{tid: "" for tid in things if isinstance(tid, str) and tid}, **labels}
    return labels, replacements


def public_text(value: Any, replacements: dict[str, str]) -> str:
    """Текст разбора для другой стороны: без скобок с таймкодами, id и ссылками на пункты разбора;
    известные id разбора — подписью человека или убраны."""
    text = _INTERNAL_NOTE.sub("", str(value if value is not None else ""))
    if replacements:
        pattern = "|".join(re.escape(key) for key in sorted(replacements, key=len, reverse=True))
        text = re.sub(rf"(?<![\w-])(?:{pattern})(?![\w-])", lambda match: replacements[match.group(0)], text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:])(?:\s*[,;])+", r"\1", text)  # от убранного id: «Владельцы:, …» → «Владельцы: …»
    return re.sub(r"\s{2,}", " ", text).strip()


def question_text(value: Any, replacements: dict[str, str]) -> str:
    """Формулировка открытого вопроса для другой стороны: сам вопрос (до первого «?») или первое предложение
    темы — без пояснений разбора, таймкодов и id."""
    text = public_text(value, replacements)
    return sentence(text[: text.index("?") + 1] if "?" in text else short_role(text))


def build_protocol(bundle: dict[str, Any]) -> str:
    names = bundle_names(bundle)
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
            commitment.get("status"), person(commitment.get("who"), names), action_text(commitment), deadline_text(commitment),
            f"«{commitment.get('source_quote')}» (`{commitment.get('source_timestamp')}`)",
        ]))
    if not bundle["commitments"]:
        lines.append(row(["—", "—", "Обязательства не зафиксированы", "—", "—"]))

    lines += ["", "## Открытые вопросы", ""]
    if bundle["open_questions"]:
        for question in bundle["open_questions"]:
            lines.append(f"- {clause(question['question'])} — «{question['quote']}» (`{question['timestamp']}`).")
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
    """Черновик другой стороне. Принятые решения (по статусному префиксу) — с сутью; открытые — только
    формулировкой в «Остаются открытыми». Итог разбора, состояние открытых решений с оценками разбора, риски
    и очередь проверки (выводы низкой уверенности, манипуляции, id) — внутренние: они только в протоколе."""
    names, ids = public_names(bundle)
    status_labels = {"open": "открыто", "in_progress": "в работе", "overdue": "просрочено", "agreed": "согласовано", "formalized": "оформлено", "blocked": "заблокировано"}
    lines = [
        "# Черновик follow-up сообщения",
        "",
        f"_Статус: `{DELIVERY_STATUS}`. Не отправлено. Адресаты не определены автоматически._",
        "",
        "Коллеги, фиксирую итоги встречи.",
    ]
    decided, pending = split_decisions(bundle)
    if decided:
        lines += ["", "Принятые решения:"]
        lines.extend(f"- {taken_text(decision, ids)}" for decision in decided)
    # действующие обязательства обеих сторон; ниже probable — не показываются (в протоколе — с пометкой)
    active = [item for item in bundle["commitments"] if item.get("status") not in INACTIVE_COMMITMENT_STATUSES and not low_confidence(item)]
    if active:
        lines += ["", "По действиям:"]
        for commitment in active:
            status = status_labels.get(commitment.get("status"), commitment.get("status"))
            deadline = deadline_text(commitment)
            deadline_clause = f"срок {deadline}" if deadline else "срок не определён"
            lines.append(f"- {public_text(commitment_label(commitment, names), ids)}; {deadline_clause}; статус: {status}.")
    # открытые решения — только формулировка, без состояния (оценки разбора — в протоколе); затем вопросы
    still_open = [sentence(public_text(item.get("decision"), ids)) for item in pending]
    still_open += [question_text(item.get("question"), ids) for item in bundle["open_questions"] if isinstance(item, dict)]
    # решение 09 и вопрос 07 с одной формулировкой (разница — «.» или «?» в конце) — один пункт
    unique: dict[str, str] = {}
    for item in still_open:
        if item:
            unique.setdefault(item.rstrip(".?!… ").lower(), item)
    still_open = list(unique.values())
    if still_open:
        lines += ["", "Остаются открытыми:"]
        lines.extend(f"- {item}" for item in still_open)
    lines += [
        "",
        "Следующий шаг: прошу подтвердить договорённости и сроки выше. Если формулировки отражены неверно, "
        "укажите конкретный пункт для исправления.",
        "",
    ]
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
