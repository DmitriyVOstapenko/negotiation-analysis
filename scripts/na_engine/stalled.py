"""Зависшие вопросы — то, что повторяется без движения. Только чтение канонических данных.

Три правила (пороги — константы ниже):

1. **Тема без решения.** Тема или открытый вопрос отмечены нерешёнными на двух и более разных
   встречах (открытый вопрос в 07, область решения в 09, статус темы open/blocked/overdue в 01),
   а в реестре решений нет решения по этой теме со статусом confirmed или rejected.
   Связь решения с темой — поле записи (linked_topic, topic_id, topic, topics) или совпадение
   цитаты решения с цитатой области решения в карте решений встречи.
2. **Повторный перенос срока.** Срок обязательства менялся с одной даты на другую два и более
   раза — по снимкам журнала событий или по повторным извлечениям того же id на встречах.
3. **Обещание без результата после напоминаний.** Открытое обязательство другой стороны
   поднималось на двух и более встречах после той, где его дали (тот же id в 07 более поздней
   встречи или обновление записи по итогам более поздней встречи), а результата нет.

Просрочка — другое состояние (срок прошёл); зависание — повтор без движения, даже когда новый
срок ещё не наступил. Модуль используется контрольным центром, очередью решений и ревью
контрагента.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .common import (
    NaError,
    meeting_dirs,
    meeting_meta,
    read_json_or_none,
    read_jsonl,
    read_text,
    relative,
    yaml_load,
)
from .registries import organization_dirs, people_names


STALLED_TOPIC_MIN_MEETINGS = 2
RESCHEDULE_MIN = 2
REMINDER_MIN_MEETINGS = 2
RESOLVING_DECISION_STATUSES = {"confirmed", "rejected"}
UNRESOLVED_TOPIC_STATUSES = {"open", "blocked", "overdue"}
CLOSED_TOPIC_STATUSES = {"agreed", "formalized", "done", "cancelled"}
CLOSED_CARD_STATUSES = CLOSED_TOPIC_STATUSES | {"closed", "resolved"}
DONE_COMMITMENT_STATUSES = {"done", "fulfilled"}
TERMINAL_COMMITMENT_STATUSES = DONE_COMMITMENT_STATUSES | {"cancelled"}
DOCUMENT_SOURCE_TYPES = {"document", "spreadsheet", "correspondence"}
LEDGER_RELATIVE = "knowledge/events/ledger.jsonl"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

RULES = {
    "stalled_topic": f"тема или открытый вопрос нерешённые на ≥ {STALLED_TOPIC_MIN_MEETINGS} встречах; в реестре решений нет решения по теме со статусом confirmed/rejected",
    "commitment_rescheduled": f"срок обязательства менялся ≥ {RESCHEDULE_MIN} раз (журнал событий или повторные извлечения на встречах)",
    "unanswered_promise": f"открытое обещание другой стороны поднималось ≥ {REMINDER_MIN_MEETINGS} раз на встречах после той, где его дали; результата нет",
}


# ---------------------------------------------------------------------------
# Общие чтения
# ---------------------------------------------------------------------------


def as_date(value: Any) -> date | None:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(value or "").casefold())).strip()


def read_frontmatter(path: Path) -> dict[str, Any]:
    lines = read_text(path).splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return {}
    try:
        data = yaml_load("\n".join(lines[1:end]))
    except NaError:
        return {}
    return data if isinstance(data, dict) else {}


def card_heading(path: Path) -> str | None:
    for line in read_text(path).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def meeting_index(root: Path) -> list[dict[str, Any]]:
    """Встречи по дате с их артефактами (только чтение)."""
    rows: list[dict[str, Any]] = []
    for path in meeting_dirs(root):
        meta = meeting_meta(path)
        meeting_date = as_date(meta.get("date")) or as_date(path.name)
        packet = read_json_or_none(path / "source_packet.json") or {}
        source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
        analysis = path / "analysis"

        def load(name: str) -> dict[str, Any]:
            value = read_json_or_none(analysis / name)
            return value if isinstance(value, dict) else {}

        rows.append({
            "meeting_id": path.name,
            "date": meeting_date.isoformat() if meeting_date else None,
            "title": meta.get("title") or path.name,
            "path": path,
            "rel": relative(path, root),
            "source_type": packet.get("source_type") or source.get("source_type"),
            "provider": packet.get("provider") or source.get("provider"),
            "source_id": packet.get("source_id") or source.get("source_id"),
            "extraction": load("01_extraction.json"),
            "arguments": load("04_arguments.json"),
            "commitments": load("07_commitments.json"),
            "risks": load("08_risks.json"),
            "decisions": load("09_decision_authority.json"),
            "argument_map": load("10_argument_map.json"),
            "receipt": read_json_or_none(path / "knowledge_apply.json") or {},
        })
    rows.sort(key=lambda row: (row["date"] or "", row["meeting_id"]))
    for order, row in enumerate(rows):
        row["order"] = order
    return rows


def is_document_source(meeting: dict[str, Any]) -> bool:
    return meeting.get("source_type") in DOCUMENT_SOURCE_TYPES or meeting.get("provider") in DOCUMENT_SOURCE_TYPES


def person_display_names(root: Path, meetings: list[dict[str, Any]]) -> dict[str, str]:
    names = people_names(root)
    for meeting in meetings:
        for participant in meeting["extraction"].get("participants") or []:
            if isinstance(participant, dict) and participant.get("person_id") and participant.get("full_name"):
                names.setdefault(participant["person_id"], participant["full_name"])
    return names


def side_map(root: Path, meetings: list[dict[str, Any]]) -> dict[str, bool]:
    """person_id/org_id → своя сторона (True) или другая (False) по извлечениям встреч и карточкам людей."""
    result: dict[str, bool] = {}
    org_side: dict[str, bool] = {}
    for meeting in meetings:
        for company in meeting["extraction"].get("companies") or []:
            if isinstance(company, dict) and company.get("id") and isinstance(company.get("is_our_side"), bool):
                org_side[company["id"]] = company["is_our_side"]
        for participant in meeting["extraction"].get("participants") or []:
            if isinstance(participant, dict) and participant.get("person_id") and isinstance(participant.get("is_our_side"), bool):
                result[participant["person_id"]] = participant["is_our_side"]
    people_dir = root / "knowledge" / "people"
    if people_dir.is_dir():
        for path in sorted(people_dir.glob("*.md")):
            front = read_frontmatter(path)
            person_id = str(front.get("id") or path.stem)
            if person_id in result:
                continue
            organizations = front.get("organizations") if isinstance(front.get("organizations"), list) else []
            sides = {org_side[org] for org in organizations if org in org_side}
            if len(sides) == 1:
                result[person_id] = sides.pop()
    result.update({org: side for org, side in org_side.items() if org not in result})
    return result


def registry_commitments(root: Path) -> list[dict[str, Any]]:
    rows = [dict(record, register_view="open") for record in read_jsonl(root / "knowledge/commitments/open.jsonl")]
    rows += [dict(record, register_view="closed") for record in read_jsonl(root / "knowledge/commitments/closed.jsonl")]
    return rows


# ---------------------------------------------------------------------------
# 1. Темы без решения
# ---------------------------------------------------------------------------


def _signal(meeting: dict[str, Any], root: Path, kind: str, text: str, quote: Any, timestamp: Any, filename: str, **extra: Any) -> dict[str, Any]:
    return {
        "meeting_id": meeting["meeting_id"],
        "date": meeting["date"],
        "order": meeting["order"],
        "kind": kind,
        "text": text,
        "quote": quote,
        "timestamp": timestamp,
        "source_path": relative(meeting["path"] / "analysis" / filename, root),
        **extra,
    }


def topic_mentions(root: Path, meetings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    topics: dict[str, dict[str, Any]] = {}

    def entry(key: str, topic_id: str | None, title: str | None) -> dict[str, Any]:
        value = topics.setdefault(key, {"key": key, "topic_id": topic_id, "title": title, "signals": [], "statuses": []})
        if title and (not value["title"] or value["title"] == key):
            value["title"] = title
        return value

    for meeting in meetings:
        for topic in meeting["extraction"].get("topics") or []:
            if not isinstance(topic, dict) or not topic.get("id"):
                continue
            item = entry(str(topic["id"]), str(topic["id"]), topic.get("title"))
            item["statuses"].append({"order": meeting["order"], "meeting_id": meeting["meeting_id"], "status": topic.get("status")})
            if topic.get("status") in UNRESOLVED_TOPIC_STATUSES:
                item["signals"].append(_signal(meeting, root, "topic_status", f"статус темы на встрече: {topic.get('status')}", topic.get("source_quote"), topic.get("source_timestamp"), "01_extraction.json"))
        for question in meeting["commitments"].get("open_questions") or []:
            if not isinstance(question, dict) or not question.get("question"):
                continue
            topic_id = question.get("topic")
            if topic_id and topic_id != "other":
                item = entry(str(topic_id), str(topic_id), None)
            else:
                item = entry("question:" + norm_text(question["question"]), None, str(question["question"]))
            item["signals"].append(_signal(meeting, root, "open_question", f"открытый вопрос: {question['question']}", question.get("quote"), question.get("timestamp"), "07_commitments.json", question=str(question["question"])))
        for area in meeting["decisions"].get("decision_areas") or []:
            if not isinstance(area, dict) or not area.get("decision"):
                continue
            topic_id = area.get("topic")
            item = entry(str(topic_id), str(topic_id), None) if topic_id and topic_id != "other" else entry("decision:" + norm_text(area["decision"]), None, str(area["decision"]))
            evidence = next((ev for ev in area.get("evidence") or [] if isinstance(ev, dict)), {})
            item["signals"].append(_signal(meeting, root, "decision_area", f"решение на столе: {area['decision']} — {area.get('current_state') or 'состояние не указано'}", evidence.get("quote"), evidence.get("timestamp"), "09_decision_authority.json", deadline=area.get("deadline")))
    return topics


def decision_topic_links(root: Path, meetings: list[dict[str, Any]], decisions: list[dict[str, Any]], known_topics: set[str]) -> dict[str, list[dict[str, Any]]]:
    """topic_id → решения реестра, связанные с темой (с указанием способа связи)."""
    area_quotes: dict[str, set[str]] = {}
    for meeting in meetings:
        for area in meeting["decisions"].get("decision_areas") or []:
            if not isinstance(area, dict) or not area.get("topic"):
                continue
            for evidence in area.get("evidence") or []:
                if isinstance(evidence, dict) and evidence.get("quote"):
                    area_quotes.setdefault(str(evidence["quote"]).strip(), set()).add(str(area["topic"]))
    links: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions:
        explicit: list[str] = [str(decision[key]) for key in ("linked_topic", "topic_id", "topic") if isinstance(decision.get(key), str) and decision.get(key)]
        if isinstance(decision.get("topics"), list):
            explicit += [str(topic) for topic in decision["topics"] if topic]
        if isinstance(decision.get("scope"), str) and decision["scope"] in known_topics:
            explicit.append(decision["scope"])
        found = [(topic, "поле записи") for topic in dict.fromkeys(explicit)]
        if not found:
            for evidence in decision.get("evidence") or []:
                quote = str(evidence.get("quote") or "").strip() if isinstance(evidence, dict) else ""
                for topic in sorted(area_quotes.get(quote, set())):
                    found.append((topic, "цитата совпадает с картой решений встречи"))
        for topic, how in dict.fromkeys(found):
            links.setdefault(topic, []).append({
                "id": decision.get("id"),
                "status": decision.get("status"),
                "statement": decision.get("statement"),
                "review_date": decision.get("review_date"),
                "meeting_id": decision.get("meeting_id"),
                "link": how,
            })
    return links


def stalled_topics(root: Path, meetings: list[dict[str, Any]], as_of: date | None) -> list[dict[str, Any]]:
    mentions = topic_mentions(root, meetings)
    decisions = read_jsonl(root / "knowledge/decisions/register.jsonl")
    links = decision_topic_links(root, meetings, decisions, {key for key, value in mentions.items() if value["topic_id"]})
    topics_dir = root / "knowledge" / "topics"
    result: list[dict[str, Any]] = []
    for key, item in mentions.items():
        signal_meetings = list(dict.fromkeys(signal["meeting_id"] for signal in sorted(item["signals"], key=lambda s: s["order"])))
        if len(signal_meetings) < STALLED_TOPIC_MIN_MEETINGS:
            continue
        last_signal_order = max(signal["order"] for signal in item["signals"])
        last_status = max(item["statuses"], key=lambda s: s["order"]) if item["statuses"] else None
        if last_status and last_status["status"] in CLOSED_TOPIC_STATUSES and last_status["order"] > last_signal_order:
            continue  # на более поздней встрече тема отмечена закрытой — не висит
        card_path = topics_dir / f"{item['topic_id']}.md" if item["topic_id"] else None
        card = read_frontmatter(card_path) if card_path and card_path.is_file() else {}
        if str(card.get("status") or "") in CLOSED_CARD_STATUSES:
            continue
        linked = links.get(item["topic_id"] or "", [])
        if any(decision.get("status") in RESOLVING_DECISION_STATUSES for decision in linked):
            continue
        title = item["title"] or (card_heading(card_path) if card_path and card_path.is_file() else None) or key
        dates = [signal["date"] for signal in item["signals"] if signal["date"]]
        first = as_date(min(dates)) if dates else None
        meetings_view = []
        for meeting_id in signal_meetings:
            signals = [signal for signal in item["signals"] if signal["meeting_id"] == meeting_id]
            meetings_view.append({"meeting_id": meeting_id, "date": signals[0]["date"], "signals": [{key_: signal.get(key_) for key_ in ("kind", "text", "quote", "timestamp", "source_path", "deadline") if signal.get(key_) is not None} for signal in signals]})
        questions = list(dict.fromkeys(signal["question"] for signal in item["signals"] if signal["kind"] == "open_question" and not key.startswith("question:")))
        result.append({
            "key": key,
            "topic_id": item["topic_id"],
            "title": title,
            "questions": questions,
            "meetings_count": len(signal_meetings),
            "meetings": meetings_view,
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
            "last_meeting_id": signal_meetings[-1],
            "days_open": (as_of - first).days if as_of and first else None,
            "register_decisions": linked,
            "topic_card": relative(card_path, root) if card_path and card_path.is_file() else None,
            "topic_card_status": card.get("status"),
        })
    result.sort(key=lambda row: (-row["meetings_count"], row["first_date"] or "", row["key"]))
    return result


# ---------------------------------------------------------------------------
# 2. Переносы сроков
# ---------------------------------------------------------------------------


def ledger_event_meetings(meetings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """event_id → встреча, по итогам которой событие записано (квитанции knowledge_apply.json)."""
    result: dict[str, dict[str, Any]] = {}
    for meeting in meetings:
        for entry in meeting["receipt"].get("history") or [] if isinstance(meeting["receipt"], dict) else []:
            for event_id in entry.get("ledger_event_ids") or [] or []:
                result[event_id] = {"meeting_id": meeting["meeting_id"], "applied_at": entry.get("applied_at")}
    return result


def _deadline_changes(sequence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    changes: list[dict[str, Any]] = []
    chain: list[str] = []
    previous: str | None = None
    for snapshot in sequence:
        deadline = snapshot.get("deadline")
        if not deadline:
            continue
        if previous is None:
            chain.append(deadline)
        elif deadline != previous:
            changes.append({"from": previous, "to": deadline, **{key: value for key, value in snapshot.items() if key != "deadline" and value is not None}})
            chain.append(deadline)
        previous = deadline
    return changes, chain


def deadline_histories(root: Path, meetings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """id обязательства → история срока по журналу событий и по извлечениям встреч."""
    event_meetings = ledger_event_meetings(meetings)
    raw: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for event in read_jsonl(root / LEDGER_RELATIVE):
        if event.get("entity_type") != "commitment" or not isinstance(event.get("payload"), dict):
            continue
        payload = event["payload"]
        origin = event_meetings.get(str(event.get("event_id")), {})
        raw.setdefault(str(event.get("entity_id")), {"ledger": [], "meetings": []})["ledger"].append({
            "deadline": payload.get("deadline"),
            "recorded_at": event.get("recorded_at"),
            "event_id": event.get("event_id"),
            "meeting_id": origin.get("meeting_id"),
            "deadline_verbatim": payload.get("deadline_verbatim"),
            "change_reason": payload.get("change_reason"),
        })
    for meeting in meetings:
        for commitment in meeting["commitments"].get("commitments") or []:
            if isinstance(commitment, dict) and commitment.get("id"):
                raw.setdefault(str(commitment["id"]), {"ledger": [], "meetings": []})["meetings"].append({
                    "deadline": commitment.get("deadline"),
                    "meeting_id": meeting["meeting_id"],
                    "date": meeting["date"],
                    "deadline_verbatim": commitment.get("deadline_verbatim"),
                    "quote": commitment.get("source_quote"),
                    "timestamp": commitment.get("source_timestamp"),
                })
    result: dict[str, dict[str, Any]] = {}
    for commitment_id, sequences in raw.items():
        ledger_changes, ledger_chain = _deadline_changes(sequences["ledger"])
        meeting_changes, meeting_chain = _deadline_changes(sequences["meetings"])
        use_ledger = len(ledger_changes) >= len(meeting_changes)
        result[commitment_id] = {
            "reschedules": max(len(ledger_changes), len(meeting_changes)),
            "basis": ("журнал событий" if use_ledger else "извлечения встреч") if (ledger_changes or meeting_changes) else None,
            "chain": ledger_chain if use_ledger else meeting_chain,
            "changes": ledger_changes if use_ledger else meeting_changes,
            "ledger_changes": ledger_changes,
            "meeting_changes": meeting_changes,
            "ledger_snapshots": len(sequences["ledger"]),
        }
    return result


def rescheduled_commitments(root: Path, meetings: list[dict[str, Any]], histories: dict[str, dict[str, Any]], names: dict[str, str], as_of: date | None, include_closed: bool = False) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in registry_commitments(root):
        if record["register_view"] == "closed" and not include_closed:
            continue
        history = histories.get(str(record.get("id")))
        if not history or history["reschedules"] < RESCHEDULE_MIN:
            continue
        deadline = as_date(record.get("deadline"))
        who = record.get("who") or record.get("from_party")
        result.append({
            "id": record.get("id"),
            "what": record.get("what") or record.get("statement"),
            "who": who,
            "owner_name": names.get(who, who or "не определён"),
            "meeting_id": record.get("meeting_id"),
            "status": record.get("status"),
            "register_view": record["register_view"],
            "deadline": record.get("deadline"),
            "is_overdue": bool(record["register_view"] == "open" and deadline and as_of and deadline < as_of),
            "reschedules": history["reschedules"],
            "basis": history["basis"],
            "chain": history["chain"],
            "changes": history["changes"],
            "source_quote": record.get("source_quote"),
            "source_timestamp": record.get("source_timestamp"),
        })
    result.sort(key=lambda row: (-row["reschedules"], str(row["id"])))
    return result


# ---------------------------------------------------------------------------
# 3. Обещания другой стороны после напоминаний
# ---------------------------------------------------------------------------


def promise_reminders(record: dict[str, Any], meetings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """встреча → эпизоды, где обязательство снова поднималось (после встречи, где его дали)."""
    commitment_id = str(record.get("id"))
    origin = record.get("meeting_id")
    origin_order = next((meeting["order"] for meeting in meetings if meeting["meeting_id"] == origin), -1)
    reminders: dict[str, list[dict[str, Any]]] = {}
    for meeting in meetings:
        if meeting["meeting_id"] == origin or meeting["order"] <= origin_order or is_document_source(meeting):
            continue
        for commitment in meeting["commitments"].get("commitments") or []:
            if isinstance(commitment, dict) and str(commitment.get("id")) == commitment_id:
                reminders.setdefault(meeting["meeting_id"], []).append({"kind": "обязательство снова на встрече", "date": meeting["date"], "quote": commitment.get("source_quote"), "timestamp": commitment.get("source_timestamp"), "deadline": commitment.get("deadline")})
        receipt = meeting["receipt"] if isinstance(meeting["receipt"], dict) else {}
        for entry in receipt.get("history") or []:
            for item in entry.get("records") or [] or []:
                if str(item.get("id")) == commitment_id and item.get("action") == "update":
                    reminders.setdefault(meeting["meeting_id"], []).append({"kind": "запись обновлена по итогам встречи", "date": meeting["date"], "applied_at": entry.get("applied_at")})
    return reminders


def unanswered_promises(root: Path, meetings: list[dict[str, Any]], sides: dict[str, bool], names: dict[str, str], as_of: date | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in read_jsonl(root / "knowledge/commitments/open.jsonl"):
        who = record.get("who") or record.get("from_party")
        if sides.get(str(who)) is not False:
            continue
        follow_up = record.get("follow_up") if isinstance(record.get("follow_up"), dict) else {}
        if record.get("status") in TERMINAL_COMMITMENT_STATUSES or follow_up.get("actual_completion_date"):
            continue
        reminders = promise_reminders(record, meetings)
        if len(reminders) < REMINDER_MIN_MEETINGS:
            continue
        deadline = as_date(record.get("deadline"))
        result.append({
            "id": record.get("id"),
            "what": record.get("what") or record.get("statement"),
            "who": who,
            "owner_name": names.get(str(who), str(who)),
            "meeting_id": record.get("meeting_id"),
            "status": record.get("status"),
            "deadline": record.get("deadline"),
            "is_overdue": bool(deadline and as_of and deadline < as_of),
            "reminder_meetings": len(reminders),
            "reminders": [{"meeting_id": meeting_id, "episodes": episodes} for meeting_id, episodes in reminders.items()],
            "reminder_sent": follow_up.get("reminder_sent"),
            "reminder_date": follow_up.get("reminder_date"),
            "source_quote": record.get("source_quote"),
            "source_timestamp": record.get("source_timestamp"),
        })
    result.sort(key=lambda row: (-row["reminder_meetings"], str(row["id"])))
    return result


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------


def find_stalled(root: Path, as_of: date | None = None) -> dict[str, Any]:
    meetings = meeting_index(root)
    names = person_display_names(root, meetings)
    histories = deadline_histories(root, meetings)
    topics = stalled_topics(root, meetings, as_of)
    rescheduled = rescheduled_commitments(root, meetings, histories, names, as_of)
    promises = unanswered_promises(root, meetings, side_map(root, meetings), names, as_of)
    return {
        "rules": RULES,
        "topics": topics,
        "rescheduled_commitments": rescheduled,
        "unanswered_promises": promises,
        "counts": {"topics": len(topics), "rescheduled_commitments": len(rescheduled), "unanswered_promises": len(promises), "total": len(topics) + len(rescheduled) + len(promises)},
    }


def topic_question(row: dict[str, Any]) -> str:
    questions = f" (открытые вопросы: {'; '.join('«' + text + '»' for text in row['questions'])})" if row.get("questions") else ""
    return (f"Тема «{row['title']}»{questions} остаётся нерешённой на {row['meetings_count']} встречах (последняя — {row['last_date'] or row['last_meeting_id']}), "
            "решения в реестре нет: принять решение, назначить срок решения или снять тему")


def times(count: int) -> str:
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} раза"
    return f"{count} раз"


def reschedule_question(row: dict[str, Any]) -> str:
    return (f"Срок обязательства «{row['what']}» ({row['owner_name']}) переносился {times(row['reschedules'])}: {' → '.join(row['chain'])}. "
            "Подтвердить новый срок, изменить условия или эскалировать")


def promise_question(row: dict[str, Any]) -> str:
    return (f"Обещание другой стороны «{row['what']}» ({row['owner_name']}) поднималось на {row['reminder_meetings']} встречах после того, как его дали, результата нет: "
            "решить, что делать дальше")


def queue_items(root: Path, make_item: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """Пункты очереди решений по зависшим вопросам (фабрика пунктов передаётся из review.py)."""
    stalled = find_stalled(root, None)
    items: list[dict[str, Any]] = []
    for row in stalled["topics"]:
        last = row["meetings"][-1]["signals"][-1]
        items.append(make_item("stalled_topic", row["last_meeting_id"], topic_question(row), last["source_path"], f"topic:{row['key']}", priority="high", evidence={"quote": last.get("quote"), "timestamp": last.get("timestamp")} if last.get("quote") else None))
    for row in stalled["rescheduled_commitments"]:
        items.append(make_item("commitment_rescheduled", row["meeting_id"], reschedule_question(row), "knowledge/commitments/open.jsonl", str(row["id"]), priority="high", evidence={"quote": row.get("source_quote"), "timestamp": row.get("source_timestamp")} if row.get("source_quote") else None))
    for row in stalled["unanswered_promises"]:
        items.append(make_item("unanswered_promise", row["meeting_id"], promise_question(row), "knowledge/commitments/open.jsonl", str(row["id"]), priority="high", evidence={"quote": row.get("source_quote"), "timestamp": row.get("source_timestamp")} if row.get("source_quote") else None))
    return items


def organization_cards(root: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in organization_dirs(root):
        paths.extend(path for path in sorted(directory.glob("*.md")) if path.name.casefold() != "readme.md")
    return paths
