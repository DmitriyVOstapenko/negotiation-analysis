"""Ревью контрагента за период → system/counterparty/<org>.{md,json} (производное, пересобирается).

По организации и её людям собирает из реестров, журнала событий и разборов встреч:
что они обещали и чем это подтверждено (документ / только слова пользователя / только
разговор), как мы выполняли обещания им, их позиции и изменения формулировок по датам,
риски и открытые вопросы, пробелы данных. Оценок личности и ярлыков не делает: только
записи с источниками. Ничего не пишет в knowledge/.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from .common import NaError, now_iso, read_jsonl, relative, write_json, write_text
from .stalled import (
    DOCUMENT_SOURCE_TYPES,
    DONE_COMMITMENT_STATUSES,
    LEDGER_RELATIVE,
    as_date,
    card_heading,
    deadline_histories,
    find_stalled,
    is_document_source,
    meeting_index,
    norm_text,
    organization_cards,
    promise_reminders,
    person_display_names,
    read_frontmatter,
    registry_commitments,
    side_map,
)


GROUPS = {
    "document": "Подтверждено документом",
    "user_words": "Подтверждено только словами пользователя",
    "talk_only": "Не подтверждено — есть только разговор",
    "cancelled": "Отменено",
}
OUTPUT_DIR = "system/counterparty"


# ---------------------------------------------------------------------------
# Организация и её люди
# ---------------------------------------------------------------------------


def _organizations(root: Path, meetings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    def entry(org_id: str) -> dict[str, Any]:
        return result.setdefault(org_id, {"id": org_id, "names": [], "card": None, "display": None})

    for path in organization_cards(root):
        front = read_frontmatter(path)
        org_id = str(front.get("id") or path.stem)
        item = entry(org_id)
        item["card"] = item["card"] or relative(path, root)
        heading = card_heading(path)
        item["display"] = item["display"] or front.get("display_name") or heading
        variants = front.get("speech_variants") if isinstance(front.get("speech_variants"), list) else []
        item["names"] += [str(value) for value in (front.get("display_name"), front.get("legal_name"), front.get("short_name"), heading, path.stem, *variants) if value]
    for meeting in meetings:
        for company in meeting["extraction"].get("companies") or []:
            if isinstance(company, dict) and company.get("id"):
                item = entry(str(company["id"]))
                item["display"] = item["display"] or company.get("short_name") or company.get("legal_name")
                item["names"] += [str(value) for value in (company.get("short_name"), company.get("legal_name")) if value]
    for item in result.values():
        item["display"] = item["display"] or item["id"]
    return result


def resolve_organization(root: Path, meetings: list[dict[str, Any]], query: str) -> dict[str, Any]:
    organizations = _organizations(root, meetings)
    if not organizations:
        raise NaError("В базе нет организаций: нет карточек в knowledge/organizations/ и нет организаций в разборах встреч.")
    wanted = (query or "").strip()
    if not wanted:
        raise NaError("--org: укажите id или название организации")
    by_id = [item for org_id, item in organizations.items() if org_id.casefold() == wanted.casefold()]
    if by_id:
        return by_id[0]
    needle = norm_text(wanted)
    exact = [item for item in organizations.values() if any(norm_text(name) == needle for name in item["names"])]
    partial = [item for item in organizations.values() if needle and any(needle in norm_text(name) for name in item["names"])]
    for found in (exact, partial):
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            listed = "; ".join(f"{item['id']} ({item['display']})" for item in found)
            raise NaError(f"Название «{wanted}» подходит нескольким организациям: {listed}. Укажите id. Похожие названия не объединяются без подтверждения.")
    known = "; ".join(f"{item['id']} ({item['display']})" for item in sorted(organizations.values(), key=lambda value: value["id"]))
    raise NaError(f"Организация «{wanted}» не найдена. Известны: {known}")


def organization_people(root: Path, meetings: list[dict[str, Any]], org: dict[str, Any]) -> dict[str, dict[str, Any]]:
    names = person_display_names(root, meetings)
    people: dict[str, dict[str, Any]] = {}

    def add(person_id: str, how: str) -> None:
        entry = people.setdefault(person_id, {"id": person_id, "name": names.get(person_id, person_id), "membership": [], "card": None})
        if how not in entry["membership"]:
            entry["membership"].append(how)

    people_dir = root / "knowledge" / "people"
    cards: dict[str, str] = {}
    if people_dir.is_dir():
        for path in sorted(people_dir.glob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            front = read_frontmatter(path)
            person_id = str(front.get("id") or path.stem)
            cards[person_id] = relative(path, root)
            organizations = front.get("organizations") if isinstance(front.get("organizations"), list) else []
            if org["id"] in organizations or front.get("company_id") == org["id"]:
                add(person_id, "карточка человека")
    for meeting in meetings:
        for participant in meeting["extraction"].get("participants") or []:
            if isinstance(participant, dict) and participant.get("company_id") == org["id"] and participant.get("person_id"):
                add(str(participant["person_id"]), f"участник встречи {meeting['meeting_id']}")
    if org.get("card"):
        text = (root / org["card"]).read_text(encoding="utf-8-sig")
        for link in re.findall(r"\[\[([^\]|#]+)", text):
            if link in cards:
                add(link, "карточка организации")
    for person_id, entry in people.items():
        entry["card"] = cards.get(person_id)
    return people


# ---------------------------------------------------------------------------
# Обязательства
# ---------------------------------------------------------------------------


def _document_ids(meetings: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for meeting in meetings:
        if is_document_source(meeting):
            ids.add(meeting["meeting_id"])
            if meeting.get("source_id"):
                ids.add(str(meeting["source_id"]))
    return ids


def _is_document_ref(reference: Any, document_ids: set[str]) -> bool:
    if isinstance(reference, str):
        return reference in document_ids
    if not isinstance(reference, dict):
        return False
    return bool(
        reference.get("page_or_cell")
        or reference.get("document")
        or reference.get("document_ref")
        or reference.get("source_type") in DOCUMENT_SOURCE_TYPES
        or str(reference.get("source_id")) in document_ids
        or str(reference.get("meeting_id")) in document_ids
    )


def document_evidence(record: dict[str, Any], document_ids: set[str]) -> list[Any]:
    follow_up = record.get("follow_up") if isinstance(record.get("follow_up"), dict) else {}
    found: list[Any] = []
    for value in (record.get("fulfillment_evidence"), record.get("completion_evidence"), follow_up.get("completion_evidence")):
        items = value if isinstance(value, list) else [value] if value else []
        found += [item for item in items if _is_document_ref(item, document_ids)]
    if record.get("closed_in_meeting_id") in document_ids:
        found.append({"source_id": record["closed_in_meeting_id"], "context": "закрыто по документу, зарегистрированному как источник"})
    return found


def classify(record: dict[str, Any], document_ids: set[str]) -> tuple[str, list[Any]]:
    """Группа подтверждения: document / user_words / talk_only / cancelled."""
    status = record.get("status")
    if status == "cancelled":
        return "cancelled", []
    documents = document_evidence(record, document_ids)
    if status in DONE_COMMITMENT_STATUSES:
        if documents:
            return "document", documents
        if record.get("register_view") == "closed" or record.get("closed_source") == "user_confirmation":
            return "user_words", []
        return "talk_only", []  # «выполнено» есть только в разборе встречи, пользователь не подтверждал
    return "talk_only", documents


def _commitment_row(record: dict[str, Any], group: str, documents: list[Any], names: dict[str, str], dates: dict[str, str | None], histories: dict[str, dict[str, Any]], meetings: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    follow_up = record.get("follow_up") if isinstance(record.get("follow_up"), dict) else {}
    who = record.get("who") or record.get("from_party")
    to_whom = record.get("to_whom") or record.get("to_party")
    deadline = as_date(record.get("deadline"))
    completed = as_date(follow_up.get("actual_completion_date")) or (as_date(record.get("closed_at")) if record.get("status") in DONE_COMMITMENT_STATUSES else None)
    open_now = record.get("status") not in DONE_COMMITMENT_STATUSES | {"cancelled"}
    history = histories.get(str(record.get("id"))) or {}
    return {
        "id": record.get("id"),
        "what": record.get("what") or record.get("statement"),
        "who": who,
        "who_name": names.get(str(who), who),
        "to_whom": to_whom,
        "to_whom_name": names.get(str(to_whom), to_whom) if to_whom else None,
        "meeting_id": record.get("meeting_id"),
        "made_on": dates.get(str(record.get("meeting_id"))),
        "deadline": record.get("deadline"),
        "deadline_verbatim": record.get("deadline_verbatim"),
        "status": record.get("status"),
        "origin": "реестр (закрытые)" if record.get("register_view") == "closed" else "реестр (открытые)" if record.get("register_view") == "open" else "разбор встречи (в базу не внесено)",
        "group": group,
        "documents": documents,
        "closed_source": record.get("closed_source"),
        "closed_in_meeting_id": record.get("closed_in_meeting_id"),
        "completed_on": completed.isoformat() if completed else None,
        "completion_notes": follow_up.get("completion_notes"),
        "on_time": (completed <= deadline) if completed and deadline else None,
        "is_overdue": bool(open_now and deadline and deadline < as_of),
        "reschedules": history.get("reschedules", 0),
        "deadline_chain": history.get("chain") or [],
        "raised_again_in": list(promise_reminders(record, meetings)),
        "source_quote": record.get("source_quote"),
        "source_timestamp": record.get("source_timestamp"),
    }


# ---------------------------------------------------------------------------
# Сборка ревью
# ---------------------------------------------------------------------------


def _parse_period(since: str | None, until: str | None) -> tuple[date | None, date | None]:
    def parse(value: str | None, name: str) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise NaError(f"{name}: ожидается дата YYYY-MM-DD, получено {value!r}") from None

    start, end = parse(since, "--since"), parse(until, "--until")
    if start and end and start > end:
        raise NaError("--since позже --until")
    return start, end


def build_review(root: Path, org_query: str, since: str | None = None, until: str | None = None, as_of: date | None = None) -> dict[str, Any]:
    start, end = _parse_period(since, until)
    as_of = as_of or end or date.today()
    meetings = meeting_index(root)
    org = resolve_organization(root, meetings, org_query)
    people = organization_people(root, meetings, org)
    person_ids = set(people)
    party_ids = person_ids | {org["id"]}
    sides = side_map(root, meetings)
    names = person_display_names(root, meetings)
    dates = {meeting["meeting_id"]: meeting["date"] for meeting in meetings}
    histories = deadline_histories(root, meetings)
    document_ids = _document_ids(meetings)
    gaps: list[str] = []

    def in_period(value: str | None) -> bool | None:
        day = as_date(value)
        if day is None:
            return None
        return (start is None or day >= start) and (end is None or day <= end)

    # встречи с участием организации
    scope: list[dict[str, Any]] = []
    undated: list[str] = []
    for meeting in meetings:
        companies = {str(company.get("id")) for company in meeting["extraction"].get("companies") or [] if isinstance(company, dict)}
        participants = {str(item.get("person_id")) for item in meeting["extraction"].get("participants") or [] if isinstance(item, dict)}
        if org["id"] not in companies and not participants & person_ids:
            continue
        inside = in_period(meeting["date"])
        if inside is None and (start or end):
            undated.append(meeting["meeting_id"])
            continue
        if inside is False:
            continue
        scope.append(meeting)
    scope_ids = {meeting["meeting_id"] for meeting in scope}

    # обязательства: реестры + разборы встреч периода, ещё не внесённые в базу
    records = registry_commitments(root)
    known_ids = {str(record.get("id")) for record in records}
    for meeting in scope:
        for item in meeting["commitments"].get("commitments") or []:
            if isinstance(item, dict) and str(item.get("id")) not in known_ids:
                records.append(dict(item, register_view=None))
                known_ids.add(str(item.get("id")))
    theirs: list[dict[str, Any]] = []
    ours: list[dict[str, Any]] = []
    for record in records:
        who = str(record.get("who") or record.get("from_party") or "")
        to_whom = str(record.get("to_whom") or record.get("to_party") or "")
        if who in party_ids:
            bucket = theirs
        elif to_whom in party_ids:
            bucket = ours
        else:
            continue
        made = as_date(dates.get(str(record.get("meeting_id"))))
        follow_up = record.get("follow_up") if isinstance(record.get("follow_up"), dict) else {}
        finished = as_date(follow_up.get("actual_completion_date")) or as_date(record.get("closed_at"))
        if end and made and made > end:
            continue
        if start and finished and finished < start and record.get("status") in DONE_COMMITMENT_STATUSES | {"cancelled"}:
            continue
        group, documents = classify(record, document_ids)
        bucket.append(_commitment_row(record, group, documents, names, dates, histories, meetings, as_of))
    for rows in (theirs, ours):
        rows.sort(key=lambda row: (row["made_on"] or "", str(row["id"])))

    # позиции и изменения формулировок
    topic_titles: dict[str, str] = {}
    for meeting in meetings:
        for topic in meeting["extraction"].get("topics") or []:
            if isinstance(topic, dict) and topic.get("id"):
                topic_titles[str(topic["id"])] = str(topic.get("title") or topic["id"])
    positions: list[dict[str, Any]] = []
    for meeting in scope:
        seen: set[str] = set()
        for cluster in meeting["argument_map"].get("argument_clusters") or []:
            for position in cluster.get("positions") or [] if isinstance(cluster, dict) else []:
                if not isinstance(position, dict) or str(position.get("speaker")) not in person_ids:
                    continue
                evidence = next((item for item in position.get("evidence") or [] if isinstance(item, dict)), {})
                seen.add(str(position.get("argument_id")))
                positions.append({"meeting_id": meeting["meeting_id"], "date": meeting["date"], "order": meeting["order"], "topic": cluster.get("topic"), "question": cluster.get("question"), "speaker": position.get("speaker"), "speaker_name": names.get(str(position.get("speaker")), position.get("speaker")), "position": position.get("position"), "quote": evidence.get("quote"), "timestamp": evidence.get("timestamp"), "confidence": position.get("confidence"), "source": "10_argument_map.json"})
        for argument in meeting["arguments"].get("arguments") or []:
            if not isinstance(argument, dict) or str(argument.get("speaker")) not in person_ids or str(argument.get("id")) in seen:
                continue
            evidence = next((item for item in argument.get("source_quotes") or [] if isinstance(item, dict)), {})
            positions.append({"meeting_id": meeting["meeting_id"], "date": meeting["date"], "order": meeting["order"], "topic": argument.get("topic"), "question": None, "speaker": argument.get("speaker"), "speaker_name": names.get(str(argument.get("speaker")), argument.get("speaker")), "position": argument.get("claim"), "quote": evidence.get("quote"), "timestamp": evidence.get("timestamp"), "confidence": argument.get("confidence"), "source": "04_arguments.json"})
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(positions, key=lambda item: (item["order"], str(item.get("topic")))):
        by_topic.setdefault(str(row.get("topic") or "other"), []).append(row)
    position_topics = []
    for topic, rows in by_topic.items():
        previous: dict[str, Any] | None = None
        for row in rows:
            row["changed_from"] = None
            if previous and previous["meeting_id"] != row["meeting_id"] and norm_text(previous["position"]) != norm_text(row["position"]):
                row["changed_from"] = {"position": previous["position"], "date": previous["date"], "meeting_id": previous["meeting_id"]}
            previous = row
        position_topics.append({"topic": topic, "title": topic_titles.get(topic, topic), "entries": [{key: value for key, value in row.items() if key != "order"} for row in rows], "changes": sum(1 for row in rows if row["changed_from"])})

    # риски, открытые вопросы, зависшее
    risks = []
    for record in [dict(item, register_view="active") for item in read_jsonl(root / "knowledge/risks/active.jsonl")] + [dict(item, register_view="closed") for item in read_jsonl(root / "knowledge/risks/closed.jsonl")]:
        companies = set(record.get("involved_companies") or [])
        involved = set(record.get("involved_people") or [])
        if org["id"] not in companies and not involved & person_ids:
            continue
        first_seen = as_date(dates.get(str(record.get("first_seen_meeting_id"))))
        closed_on = as_date(record.get("closed_at"))
        if end and first_seen and first_seen > end:
            continue
        if start and record["register_view"] == "closed" and closed_on and closed_on < start:
            continue
        mitigation = record.get("mitigation") if isinstance(record.get("mitigation"), dict) else {}
        evidence = next((item for item in record.get("evidence") or [] if isinstance(item, dict)), {})
        risks.append({"id": record.get("id"), "description": record.get("description") or record.get("statement"), "severity": record.get("severity"), "status": record.get("status"), "register_view": record["register_view"],
                      "owner": mitigation.get("owner"), "owner_name": names.get(str(mitigation.get("owner")), mitigation.get("owner")), "mitigation_deadline": mitigation.get("deadline"),
                      "meeting_id": evidence.get("meeting_id") or record.get("first_seen_meeting_id"), "quote": evidence.get("quote"), "timestamp": evidence.get("timestamp")})
    open_questions = []
    for meeting in scope:
        for question in meeting["commitments"].get("open_questions") or []:
            if isinstance(question, dict) and question.get("question"):
                open_questions.append({"meeting_id": meeting["meeting_id"], "date": meeting["date"], "topic": question.get("topic"), "question": question["question"], "quote": question.get("quote"), "timestamp": question.get("timestamp")})
    stalled = find_stalled(root, as_of)
    their_ids = {str(row["id"]) for row in theirs} | {str(row["id"]) for row in ours}
    stalled_view = {
        "topics": [row for row in stalled["topics"] if any(meeting["meeting_id"] in scope_ids for meeting in row["meetings"])],
        "rescheduled_commitments": [row for row in stalled["rescheduled_commitments"] if str(row["id"]) in their_ids],
        "unanswered_promises": [row for row in stalled["unanswered_promises"] if str(row["who"]) in party_ids],
    }

    # пробелы данных
    if not org.get("card"):
        gaps.append("Нет карточки организации в knowledge/organizations/: сведения только из разборов встреч.")
    if sides.get(org["id"]) is True:
        gaps.append("В разборах организация отмечена как наша сторона: «их обещания» — обещания её сотрудников.")
    if not people:
        gaps.append("Не найдено ни одного человека организации (ни в карточках людей, ни среди участников встреч).")
    for person in people.values():
        if not person["card"]:
            gaps.append(f"Нет карточки человека {person['name']} ({person['id']}) в knowledge/people/.")
    if not scope:
        gaps.append("За период нет встреч с участием организации.")
    unchecked = [meeting["meeting_id"] for meeting in meetings if not meeting["extraction"] and not is_document_source(meeting) and in_period(meeting["date"]) is not False]
    if unchecked:
        gaps.append("Встречи без разбора участников (01_extraction): участие организации не проверено — " + ", ".join(unchecked) + ".")
    if undated:
        gaps.append("Встречи без даты не учтены при отборе по периоду: " + ", ".join(undated) + ".")
    for meeting in scope:
        missing = [name for name, key in (("01_extraction", "extraction"), ("07_commitments", "commitments"), ("10_argument_map", "argument_map")) if not meeting[key]]
        if missing:
            gaps.append(f"Встреча {meeting['meeting_id']}: нет разбора {', '.join(missing)}.")
    analysis_only = sum(1 for row in theirs + ours if row["origin"].startswith("разбор"))
    if analysis_only:
        gaps.append(f"Обязательств из разборов, ещё не внесённых в базу: {analysis_only} — статусы по ним не подтверждены пользователем.")
    no_deadline = [row for row in theirs + ours if not row["deadline"] and row["group"] != "cancelled"]
    if no_deadline:
        gaps.append(f"Обязательств без срока: {len(no_deadline)} ({', '.join(str(row['id']) for row in no_deadline)}).")
    ledger = read_jsonl(root / LEDGER_RELATIVE)
    ledger_start = min((str(event.get("recorded_at"))[:10] for event in ledger if event.get("recorded_at")), default=None)
    if not ledger_start:
        gaps.append("Журнал событий пуст: история переносов сроков не восстанавливается.")
    elif start and as_date(ledger_start) and as_date(ledger_start) > start:
        gaps.append(f"Журнал событий ведётся с {ledger_start}: изменения записей до этой даты не видны.")
    if not position_topics:
        gaps.append("Позиции их сотрудников в разборах не найдены (нет 10_argument_map / 04_arguments с их репликами).")

    def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {key: sum(1 for row in rows if row["group"] == key) for key in GROUPS}

    dated = sorted(meeting["date"] for meeting in scope if meeting["date"])
    return {
        "view_type": "counterparty_review",
        "view_version": "1.0",
        "generated_at": now_iso(),
        "as_of_date": as_of.isoformat(),
        "period": {"since": start.isoformat() if start else None, "until": end.isoformat() if end else None},
        "organization": {"id": org["id"], "name": org["display"], "card": org.get("card"), "is_our_side": sides.get(org["id"])},
        "people": list(people.values()),
        "meetings": [{"meeting_id": meeting["meeting_id"], "date": meeting["date"], "title": meeting["title"]} for meeting in scope],
        "data_basis": {"meetings": len(scope), "first_meeting": dated[0] if dated else None, "last_meeting": dated[-1] if dated else None, "ledger_start": ledger_start, "ledger_events": len(ledger)},
        "their_commitments": theirs,
        "our_commitments": ours,
        "positions": position_topics,
        "risks": risks,
        "open_questions": open_questions,
        "stalled": stalled_view,
        "gaps": gaps,
        "summary": {
            "their_commitments": len(theirs),
            "their_by_group": counts(theirs),
            "their_overdue": sum(1 for row in theirs if row["is_overdue"]),
            "our_commitments": len(ours),
            "our_by_group": counts(ours),
            "our_overdue": sum(1 for row in ours if row["is_overdue"]),
            "our_on_time": sum(1 for row in ours if row["on_time"] is True),
            "our_late": sum(1 for row in ours if row["on_time"] is False),
            "position_topics": len(position_topics),
            "position_changes": sum(item["changes"] for item in position_topics),
            "risks": len(risks),
            "open_questions": len(open_questions),
            "stalled": sum(len(value) for value in stalled_view.values()),
            "gaps": len(gaps),
        },
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _ref(meeting_id: Any, timestamp: Any, quote: Any) -> str:
    parts = [f"[[{meeting_id}]]"] if meeting_id else []
    if timestamp:
        parts.append(f"`{timestamp}`")
    if quote:
        parts.append(f"«{quote}»")
    return " ".join(parts) or "источник не указан"


def _commitment_line(row: dict[str, Any], show_party: str) -> list[str]:
    party = row["who_name"] if show_party == "who" else (row["to_whom_name"] or "—")
    timing = []
    if row["deadline"]:
        timing.append(f"срок {row['deadline']}")
    elif row["deadline_verbatim"]:
        timing.append(f"срок словами: «{row['deadline_verbatim']}», даты нет")
    else:
        timing.append("срок не установлен")
    if row["completed_on"]:
        timing.append(f"выполнено {row['completed_on']}" + (" (в срок)" if row["on_time"] is True else " (позже срока)" if row["on_time"] is False else ""))
    if row["is_overdue"]:
        timing.append("просрочено")
    if row["reschedules"]:
        timing.append(f"переносов срока: {row['reschedules']} ({' → '.join(row['deadline_chain'])})")
    if row.get("raised_again_in"):
        timing.append("поднималось повторно: " + ", ".join(f"[[{meeting_id}]]" for meeting_id in row["raised_again_in"]))
    lines = [f"- **{party}** — {row['what']} (`{row['id']}`); статус {row['status']}; " + "; ".join(timing) + f"; запись: {row['origin']}."]
    lines.append(f"  - обещание: {_ref(row['meeting_id'], row['source_timestamp'], row['source_quote'])}")
    if row["group"] == "document":
        refs = "; ".join(str(item.get("source_id") or item.get("document") or item.get("document_ref") or "документ") + (f", {item['page_or_cell']}" if isinstance(item, dict) and item.get("page_or_cell") else "") if isinstance(item, dict) else str(item) for item in row["documents"])
        lines.append(f"  - подтверждение: документ — {refs}")
    elif row["group"] == "user_words":
        note = f"; пояснение: {row['completion_notes']}" if row.get("completion_notes") else ""
        lines.append(f"  - подтверждение: только слова пользователя (закрыто по подтверждению{', в ' + str(row['closed_in_meeting_id']) if row.get('closed_in_meeting_id') else ''}){note}")
    elif row["group"] == "talk_only" and row["status"] in DONE_COMMITMENT_STATUSES:
        lines.append("  - «выполнено» есть только в разборе встречи; пользователь и документ это не подтверждали")
    return lines


def build_markdown(review: dict[str, Any]) -> str:
    org = review["organization"]
    basis = review["data_basis"]
    summary = review["summary"]
    period = review["period"]
    period_text = (f"с {period['since']}" if period["since"] else "с начала базы") + (f" по {period['until']}" if period["until"] else " по сегодня")
    span = f" ({basis['first_meeting']} — {basis['last_meeting']})" if basis["first_meeting"] else ""
    short = [gap.rstrip(".")[:1].lower() + gap.rstrip(".")[1:] for gap in review["gaps"][:3]]
    missing = "; ".join(short) + (f"; ещё пробелов: {len(review['gaps']) - 3} — см. раздел 5" if len(review["gaps"]) > 3 else "") if review["gaps"] else "существенных пробелов не найдено"
    lines = [
        f"# Ревью контрагента: {org['name']}",
        "",
        f"**Данные:** встреч с участием организации за период: {basis['meetings']}{span}; реестры обязательств и рисков на {review['generated_at'][:10]}; "
        f"журнал событий {'с ' + basis['ledger_start'] if basis['ledger_start'] else 'пуст'}; разборы встреч (01, 04, 07, 09, 10). Нет / неполно: {missing}.",
        "",
        f"**Период:** {period_text} · **Организация:** `{org['id']}`" + (f" ([[{Path(org['card']).stem}]])" if org.get("card") else " (карточки нет)") + f" · **Сформировано:** {review['generated_at']}",
        "",
        "Люди организации: " + (", ".join(f"{person['name']} (`{person['id']}`)" for person in review["people"]) or "не найдены") + ".",
        "",
        "## Кратко",
        "",
        "| Показатель | Значение |",
        "|---|---|",
        f"| Их обещаний | {summary['their_commitments']} (документом {summary['their_by_group']['document']}, только словами пользователя {summary['their_by_group']['user_words']}, только разговор {summary['their_by_group']['talk_only']}, отменено {summary['their_by_group']['cancelled']}) |",
        f"| Из них просрочено | {summary['their_overdue']} |",
        f"| Наших обещаний им | {summary['our_commitments']} (в срок {summary['our_on_time']}, позже срока {summary['our_late']}, просрочено сейчас {summary['our_overdue']}) |",
        f"| Темы с позициями / изменения формулировок | {summary['position_topics']} / {summary['position_changes']} |",
        f"| Риски / открытые вопросы / зависшее | {summary['risks']} / {summary['open_questions']} / {summary['stalled']} |",
        f"| Пробелы данных | {summary['gaps']} |",
        "",
        "## 1. Что они обещали",
        "",
    ]
    theirs = review["their_commitments"]
    if not theirs:
        lines.append("- Обещаний их стороны в базе и разборах за период нет.")
    for group, title in GROUPS.items():
        rows = [row for row in theirs if row["group"] == group]
        if not rows:
            continue
        lines += [f"### {title} ({len(rows)})", ""]
        for row in rows:
            lines += _commitment_line(row, "who")
        lines.append("")
    lines += ["## 2. Как мы выполняли обещания им", ""]
    ours = review["our_commitments"]
    if not ours:
        lines.append("- Наших обещаний им в базе и разборах за период нет.")
    for group, title in GROUPS.items():
        rows = [row for row in ours if row["group"] == group]
        if not rows:
            continue
        lines += [f"### {title} ({len(rows)})", ""]
        for row in rows:
            lines += _commitment_line(row, "who")
        lines.append("")
    lines += ["", "## 3. Позиции и их изменения", "", "_Изменение — другая формулировка позиции по той же теме на более поздней встрече; причины не приписываются._", ""]
    if not review["positions"]:
        lines.append("- Позиции их сотрудников в разборах за период не найдены.")
    for topic in review["positions"]:
        lines += [f"### {topic['title']} (`{topic['topic']}`)", ""]
        for entry in topic["entries"]:
            change = f" — **формулировка изменилась** (было {entry['changed_from']['date'] or entry['changed_from']['meeting_id']}: «{entry['changed_from']['position']}»)" if entry.get("changed_from") else ""
            lines.append(f"- {entry['date'] or '—'}, {entry['speaker_name']}: {entry['position']} (confidence: {entry.get('confidence') or 'не указана'}){change}")
            lines.append(f"  - источник: {_ref(entry['meeting_id'], entry.get('timestamp'), entry.get('quote'))}")
        lines.append("")
    lines += ["## 4. Риски и открытые вопросы", "", "### Риски", ""]
    if not review["risks"]:
        lines.append("- Рисков с их участием в реестрах нет.")
    for risk in review["risks"]:
        lines.append(f"- `{risk['id']}` [{risk.get('severity') or 'важность не указана'}, {risk.get('status')}] {risk['description']} Владелец митигации: {risk.get('owner_name') or 'не назначен'}; срок: {risk.get('mitigation_deadline') or 'не установлен'}.")
        lines.append(f"  - источник: {_ref(risk.get('meeting_id'), risk.get('timestamp'), risk.get('quote'))}")
    lines += ["", "### Открытые вопросы встреч", ""]
    if not review["open_questions"]:
        lines.append("- Открытых вопросов в разборах встреч за период нет.")
    for question in review["open_questions"]:
        lines.append(f"- {question['date'] or '—'}: {question['question']} — {_ref(question['meeting_id'], question.get('timestamp'), question.get('quote'))}")
    stalled = review["stalled"]
    lines += ["", "### Зависшее (по правилам контрольного центра)", ""]
    if not any(stalled.values()):
        lines.append("- Нет.")
    for row in stalled["topics"]:
        lines.append(f"- Тема «{row['title']}» нерешённая на {row['meetings_count']} встречах ({', '.join(meeting['date'] or meeting['meeting_id'] for meeting in row['meetings'])}); решения в реестре нет.")
    for row in stalled["rescheduled_commitments"]:
        lines.append(f"- Срок «{row['what']}» ({row['owner_name']}) переносился: {' → '.join(row['chain'])}.")
    for row in stalled["unanswered_promises"]:
        lines.append(f"- Обещание «{row['what']}» ({row['owner_name']}) поднималось на {row['reminder_meetings']} встречах без результата.")
    lines += ["", "## 5. Пробелы данных", ""]
    lines += [f"- {gap}" for gap in review["gaps"]] or ["- Существенных пробелов не найдено."]
    lines += [
        "",
        "---",
        "",
        "_Производное представление: пересобирается командой `na.py counterparty`, источники истины — negotiations/ и knowledge/. "
        "Здесь только записи с источниками: без оценок личности, ярлыков и выводов о мотивах._",
        "",
    ]
    return "\n".join(lines)


def run_counterparty(root: Path, org_query: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    review = build_review(root, org_query, since, until)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", review["organization"]["id"]).strip("-.") or "organization"
    json_path = root / OUTPUT_DIR / f"{safe}.json"
    md_path = root / OUTPUT_DIR / f"{safe}.md"
    write_json(json_path, review)
    write_text(md_path, build_markdown(review))
    return {"valid": True, "outputs": [relative(json_path, root), relative(md_path, root)], **review}


def summary_lines(result: dict[str, Any]) -> list[str]:
    summary = result["summary"]
    their = summary["their_by_group"]
    return [
        f"Ревью контрагента «{result['organization']['name']}» собрано: {result['outputs'][1]} (и .json).",
        f"Их обещаний {summary['their_commitments']}: документом {their['document']}, только словами пользователя {their['user_words']}, только разговор {their['talk_only']}, отменено {their['cancelled']}; просрочено {summary['their_overdue']}.",
        f"Наших обещаний им {summary['our_commitments']}: в срок {summary['our_on_time']}, позже срока {summary['our_late']}, просрочено сейчас {summary['our_overdue']}.",
        f"Позиции: тем {summary['position_topics']}, изменений формулировок {summary['position_changes']}; рисков {summary['risks']}; открытых вопросов {summary['open_questions']}; зависшее {summary['stalled']}; пробелов данных {summary['gaps']}.",
    ]
