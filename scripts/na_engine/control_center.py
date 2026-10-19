"""Системная проверка workspace и сборка контрольного центра (system/control_center.md + .json).

Контрольный центр — производное представление: его можно удалить и пересобрать
из negotiations/ и knowledge/. Источники артефактов встреч не изменяются.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .common import (
    PLUGIN_ROOT,
    NaError,
    gate_state,
    meeting_dirs,
    meeting_meta,
    now_iso,
    read_json_or_none,
    read_jsonl,
    read_yaml,
    relative,
    write_json,
    write_text,
    yaml_scalar,
)
from .analysis_profile import meeting_profile, required_analysis_files
from .registries import organization_dirs, people_names
from .review import build_queue


OPEN_COMMITMENT_STATUSES = {"open", "in_progress", "blocked", "agreed", "formalized", "overdue"}
TERMINAL_COMMITMENT_STATUSES = {"done", "cancelled"}
STATUS_LABELS = {
    "ready": "готова",
    "incomplete_input": "неполный вход",
    "blocked_by_gate": "заблокирована шлюзом согласия",
    "awaiting_gate": "ожидает шлюза согласия",
    "analysis_incomplete": "анализ не завершён",
    "report_incomplete": "отчёт не завершён",
    "knowledge_update_incomplete": "база не обновлена",
}


def add_issue(issues: list[dict[str, Any]], severity: str, code: str, message: str, *, path: str | None = None, entity_id: str | None = None) -> None:
    issue: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if path:
        issue["path"] = path
    if entity_id:
        issue["entity_id"] = entity_id
    issues.append(issue)


def load_registry(path: Path, root: Path, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not path.is_file():
        add_issue(issues, "warning", "missing_registry", "Реестр отсутствует; будет создан при первом применении предложения", path=relative(path, root))
        return []
    try:
        rows = read_jsonl(path)
    except Exception as exc:  # noqa: BLE001 — ошибка формата должна попасть в отчёт, а не уронить сборку
        add_issue(issues, "error", "invalid_jsonl", str(exc), path=relative(path, root))
        return []
    return rows


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def safe_cell(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def table_row(values: list[Any]) -> str:
    return "| " + " | ".join(safe_cell(value) for value in values) + " |"


def ontology_version() -> str | None:
    """Версия онтологии из core/ontology/*.yaml; сложный YAML вне подмножества читается регулярным выражением."""
    ontology_dir = PLUGIN_ROOT / "core" / "ontology"
    if not ontology_dir.is_dir():
        return None
    for candidate in sorted(ontology_dir.glob("*.yaml")):
        version = yaml_scalar(candidate.read_text(encoding="utf-8-sig"), "version")
        if version:
            return str(version)
    return None


def profile_summary(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("role", "organization", "policy"):
        path = root / "profile" / f"{name}.yaml"
        if path.is_file():
            try:
                data = read_yaml(path)
            except NaError:
                data = {}
            text = path.read_text(encoding="utf-8-sig")
            result[name] = data.get("id") or data.get("name") or data.get("title") or yaml_scalar(text, "id") or yaml_scalar(text, "name") or "задан"
    return result


def pipeline_status_for(meeting_path: Path, consent: str, missing_base: list[str], missing_analysis: list[str], validation_valid: bool, report_valid: bool, knowledge_done: bool) -> str:
    if missing_base:
        return "incomplete_input"
    if consent == "no":
        return "blocked_by_gate"
    if consent != "yes":
        return "awaiting_gate"
    if missing_analysis or not validation_valid:
        return "analysis_incomplete"
    if not ((meeting_path / "report.md").is_file() and (meeting_path / "report_auto.md").is_file() and report_valid):
        return "report_incomplete"
    if not knowledge_done:
        return "knowledge_update_incomplete"
    return "ready"


def knowledge_stage_done(meeting_path: Path) -> bool:
    """Обновление базы завершено: diff показан и применён (или предложение не вносит изменений)."""
    from .knowledge import DIFF_FILE, PROPOSAL_FILE, RECEIPT_FILE  # локальный импорт: избегаем цикла

    diff_path = meeting_path / DIFF_FILE
    if not diff_path.is_file():
        return False
    proposal_path = meeting_path / PROPOSAL_FILE
    if not proposal_path.is_file():
        return True  # встреча из прежней базы: diff оформлен вручную
    from .common import sha256_file

    receipt = read_json_or_none(meeting_path / RECEIPT_FILE) or {}
    proposal_sha = sha256_file(proposal_path)
    if proposal_sha in receipt.get("applied_proposals", []):
        return True
    text = diff_path.read_text(encoding="utf-8-sig")
    return "## Изменений нет" in text and f"sha256 `{proposal_sha}`" in text


def build_project_state(root: Path, as_of: date) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    review_queue = build_queue(root)
    open_queue_items = [item for item in review_queue["items"] if item.get("status") == "open"]
    queue_by_meeting: dict[str, list[dict[str, Any]]] = {}
    for queue_item in open_queue_items:
        if queue_item.get("meeting_id"):
            queue_by_meeting.setdefault(queue_item["meeting_id"], []).append(queue_item)
    person_names = people_names(root)
    person_ids = set(person_names)
    topic_ids = {path.stem for path in (root / "knowledge/topics").glob("*.md")} if (root / "knowledge/topics").is_dir() else set()
    organization_count = sum(len([p for p in directory.glob("*.md") if p.name.casefold() != "readme.md"]) for directory in organization_dirs(root))

    meetings: list[dict[str, Any]] = []
    participation: list[dict[str, Any]] = []
    meeting_ids: set[str] = set()
    if not (root / "negotiations").is_dir():
        add_issue(issues, "warning", "missing_negotiations", "Каталог negotiations/ отсутствует: встреч ещё нет", path="negotiations")

    for meeting_path in meeting_dirs(root):
        meeting_id = meeting_path.name
        meeting_ids.add(meeting_id)
        meta = meeting_meta(meeting_path)
        gate = gate_state(meeting_path)
        analysis_dir = meeting_path / "analysis"
        missing_base = [name for name in ("meta.yaml", "transcript.txt", "review.yaml") if not (meeting_path / name).is_file()]
        required_analysis = required_analysis_files(meeting_path)  # профиль разбора: full — 11, light — 01/07/08
        missing_analysis = [name for name in required_analysis if not (analysis_dir / name).is_file()]
        extraction = read_json_or_none(analysis_dir / "01_extraction.json") or {}
        effectiveness = read_json_or_none(analysis_dir / "11_meeting_effectiveness.json") or {}
        power = read_json_or_none(analysis_dir / "05_power.json") or {}
        validation = read_json_or_none(analysis_dir / "validation_report.json") or {}
        report_validation = read_json_or_none(analysis_dir / "report_auto_validation.json") or {}
        manifest = read_json_or_none(meeting_path / "run_manifest.json") or {}
        for participant in extraction.get("participants", []):
            if isinstance(participant, dict) and participant.get("person_id") and participant.get("full_name"):
                person_names.setdefault(participant["person_id"], participant["full_name"])

        validation_valid = validation.get("valid") is True
        report_valid = report_validation.get("valid") is True
        knowledge_done = knowledge_stage_done(meeting_path)
        status = pipeline_status_for(meeting_path, gate["consent"], missing_base, missing_analysis, validation_valid, report_valid, knowledge_done)
        if status != "ready":
            add_issue(issues, "warning", "meeting_not_ready", f"Встреча имеет состояние {status}", path=relative(meeting_path, root), entity_id=meeting_id)
        if gate["consent"] != "yes":
            add_issue(issues, "warning", "consent_not_confirmed", "Шлюз согласия не пройден (consent != yes)", path=relative(meeting_path / "review.yaml", root), entity_id=meeting_id)
        if extraction.get("outcome") and not meta.get("outcome"):
            add_issue(issues, "info", "meta_outcome_empty", f"В meta.yaml результат не заполнен; аналитический результат: {extraction.get('outcome')}", path=relative(meeting_path / "meta.yaml", root), entity_id=meeting_id)

        open_review_items = [item["question"] for item in queue_by_meeting.get(meeting_id, [])]
        row = {
            "meeting_id": meeting_id,
            "title": meta["title"],
            "date": (meta.get("date") or "")[:10] or None,
            "datetime": meta.get("date"),
            "meeting_type": meta.get("meeting_type"),
            "sensitivity": gate["sensitivity"] if gate["sensitivity"] != "not_set" else meta.get("sensitivity"),
            "gate_consent": gate["consent"],
            "outcome": extraction.get("outcome"),
            "effectiveness_score": effectiveness.get("overall_score_0_5"),
            "effectiveness_classification": effectiveness.get("classification"),
            "participants_count": len(extraction.get("participants", [])),
            "analysis_files": len(required_analysis) - len(missing_analysis),
            "analysis_files_required": len(required_analysis),
            "analysis_profile": meeting_profile(meeting_path),
            "quotes_checked": validation.get("checked_quotes", 0),
            "validation_valid": validation_valid,
            "report_valid": report_valid,
            "manifest_status": manifest.get("overall_status"),
            "resume_from": manifest.get("resume_from"),
            "open_review_items": open_review_items,
            "open_review_items_count": len(open_review_items),
            "audio_available": (analysis_dir / "audio_acoustic_metrics.json").is_file(),
            "follow_up_status": (read_json_or_none(meeting_path / "follow_up.json") or {}).get("delivery_status"),
            "pipeline_status": status,
            "path": relative(meeting_path, root),
        }
        meetings.append(row)
        for share in power.get("talk_time_share", []):
            if isinstance(share, dict) and share.get("person_id") is not None:
                participation.append({"meeting_id": meeting_id, "speaker_id": share["person_id"], "speaker": person_names.get(share["person_id"], share["person_id"]), "share_pct": share.get("share_pct")})
    meetings.sort(key=lambda item: (item.get("datetime") or "", item["meeting_id"]))

    open_path = root / "knowledge/commitments/open.jsonl"
    closed_path = root / "knowledge/commitments/closed.jsonl"
    active_risks_path = root / "knowledge/risks/active.jsonl"
    closed_risks_path = root / "knowledge/risks/closed.jsonl"
    open_commitments = load_registry(open_path, root, issues)
    closed_commitments = load_registry(closed_path, root, issues)
    active_risks = load_registry(active_risks_path, root, issues)
    closed_risks = load_registry(closed_risks_path, root, issues)
    decisions = read_jsonl(root / "knowledge/decisions/register.jsonl")
    assignments = read_jsonl(root / "knowledge/assignments/register.jsonl")

    for duplicate in sorted({r.get("id") for r in open_commitments} & {r.get("id") for r in closed_commitments} - {None}):
        add_issue(issues, "error", "commitment_in_open_and_closed", "Обязательство одновременно в открытом и закрытом реестре", entity_id=str(duplicate))
    enriched_open: list[dict[str, Any]] = []
    for row in open_commitments:
        commitment_id = str(row.get("id") or "unknown")
        status = row.get("status")
        if status not in OPEN_COMMITMENT_STATUSES:
            add_issue(issues, "warning", "unexpected_open_status", f"Статус {status!r} не соответствует открытому реестру", path=relative(open_path, root), entity_id=commitment_id)
        if row.get("meeting_id") not in meeting_ids:
            add_issue(issues, "error", "missing_meeting_ref", "Не найдена исходная встреча", entity_id=commitment_id)
        if row.get("who") and row.get("who") not in person_ids:
            add_issue(issues, "info", "missing_person_ref", "Нет досье ответственного в knowledge/people", entity_id=commitment_id)
        linked_topic = row.get("linked_topic")
        if linked_topic and linked_topic != "other" and linked_topic not in topic_ids:
            add_issue(issues, "info", "missing_topic_ref", "Нет карточки связанной темы в knowledge/topics", entity_id=commitment_id)
        deadline = parse_date(row.get("deadline"))
        is_overdue = status == "overdue" or bool(deadline and deadline < as_of)
        days_overdue = (as_of - deadline).days if deadline and deadline < as_of else 0
        follow_up = row.get("follow_up") if isinstance(row.get("follow_up"), dict) else {}
        enriched_open.append({**row, "owner_name": person_names.get(row.get("who"), row.get("who") or "не определён"), "is_overdue": is_overdue, "days_overdue": days_overdue, "control_date": follow_up.get("reminder_date")})
    enriched_closed: list[dict[str, Any]] = []
    for row in closed_commitments:
        commitment_id = str(row.get("id") or "unknown")
        if row.get("status") not in TERMINAL_COMMITMENT_STATUSES:
            add_issue(issues, "warning", "unexpected_closed_status", f"Статус {row.get('status')!r} не соответствует закрытому реестру", path=relative(closed_path, root), entity_id=commitment_id)
        follow_up = row.get("follow_up") if isinstance(row.get("follow_up"), dict) else {}
        completion = follow_up.get("actual_completion_date") or row.get("actual_completion_date") or row.get("closed_at")
        if not completion:
            add_issue(issues, "info", "completion_date_unknown", "Фактическая дата выполнения не установлена", entity_id=commitment_id)
        enriched_closed.append({**row, "owner_name": person_names.get(row.get("who"), row.get("who") or "не определён"), "actual_completion_date": completion})

    for duplicate in sorted({r.get("id") for r in active_risks} & {r.get("id") for r in closed_risks} - {None}):
        add_issue(issues, "error", "risk_in_active_and_closed", "Риск одновременно в активном и закрытом реестре", entity_id=str(duplicate))
    enriched_risks: list[dict[str, Any]] = []
    for row in active_risks:
        risk_id = str(row.get("id") or "unknown")
        if row.get("status") in {"closed", "realized"}:
            add_issue(issues, "warning", "closed_risk_in_active", "Закрытый риск находится в active.jsonl", entity_id=risk_id)
        if row.get("first_seen_meeting_id") not in meeting_ids or row.get("last_seen_meeting_id") not in meeting_ids:
            add_issue(issues, "error", "missing_risk_meeting_ref", "Не найдена встреча, указанная в риске", entity_id=risk_id)
        linked_topic = row.get("linked_topic")
        if linked_topic and linked_topic not in topic_ids:
            add_issue(issues, "info", "missing_risk_topic_ref", "Нет карточки связанной темы риска", entity_id=risk_id)
        mitigation = row.get("mitigation") if isinstance(row.get("mitigation"), dict) else {}
        owner = mitigation.get("owner")
        if owner and owner not in person_ids:
            add_issue(issues, "info", "missing_risk_owner_ref", "Нет досье владельца риска", entity_id=risk_id)
        enriched_risks.append({**row, "owner_name": person_names.get(owner, owner or "не определён"), "mitigation_deadline": mitigation.get("deadline"), "planned_actions": mitigation.get("planned_actions")})

    overdue_commitments = sorted((row for row in enriched_open if row["is_overdue"]), key=lambda item: (-item["days_overdue"], item.get("deadline") or "9999-12-31"))
    high_risks = sorted((row for row in enriched_risks if row.get("severity") == "high"), key=lambda item: item.get("mitigation_deadline") or "9999-12-31")
    incomplete_meetings = [row for row in meetings if row["pipeline_status"] != "ready"]
    review_items = [{"meeting_id": item.get("meeting_id"), "item_id": item["item_id"], "item": item["question"], "priority": item["priority"], "category": item["category"]} for item in open_queue_items]
    event_validation = read_json_or_none(root / "system/event_projection_validation.json") or {}
    search_manifest = read_json_or_none(root / "system/search_index_manifest.json") or {}
    schema_ready = all((read_json_or_none(root / row["path"] / "analysis/validation_report.json") or {}).get("schema_valid") is True for row in meetings) if meetings else True
    manifests_ready = sum(1 for row in meetings if row.get("manifest_status") == "ready")
    followup_ready = sum(1 for row in meetings if row.get("follow_up_status") == "draft_not_sent")
    capabilities = [
        {"id": "analysis_schemas", "status": "ready" if schema_ready else "attention", "detail": "JSON Schema для 11 результатов"},
        {"id": "resumable_pipeline", "status": "ready" if manifests_ready == len(meetings) else "attention", "detail": f"готовых manifest: {manifests_ready}/{len(meetings)}"},
        {"id": "review_queue", "status": "ready", "detail": f"открыто пунктов: {review_queue['summary']['open']}"},
        {"id": "event_ledger", "status": "ready" if event_validation.get("valid") is True else "attention", "detail": "проекция совпадает с реестрами" if event_validation.get("valid") is True else "проекция не проверена или расходится"},
        {"id": "full_text_search", "status": "ready" if (root / "system/search.sqlite").is_file() else "attention", "detail": f"документов: {search_manifest.get('documents', 0)}"},
        {"id": "follow_up_generator", "status": "ready" if followup_ready == len(meetings) else "attention", "detail": f"черновиков draft_not_sent: {followup_ready}/{len(meetings)}"},
    ]
    stalled = stalled_state(root, as_of, issues)
    workspace = workspace_state(root)
    if workspace["state"] in {"needs_migration", "newer_than_plugin"}:
        add_issue(issues, "warning", f"workspace_{workspace['state']}", workspace["message"], path=workspace["file"])
    elif workspace["state"] == "invalid":
        add_issue(issues, "error", "workspace_version_invalid", workspace["message"], path=workspace["file"])
    issue_counts = {severity: sum(1 for issue in issues if issue["severity"] == severity) for severity in ("error", "warning", "info")}
    scores = [row["effectiveness_score"] for row in meetings if isinstance(row.get("effectiveness_score"), (int, float))]
    latest_score = scores[-1] if scores else None
    previous_score = scores[-2] if len(scores) >= 2 else None
    score_delta = round(latest_score - previous_score, 2) if latest_score is not None and previous_score is not None else None

    return {
        "view_type": "project_control_center",
        "view_version": "1.1",
        "generated_at": now_iso(),
        "as_of_date": as_of.isoformat(),
        "ontology_version": ontology_version(),
        "profile": profile_summary(root),
        "summary": {
            "meetings_total": len(meetings),
            "meetings_ready": len(meetings) - len(incomplete_meetings),
            "meetings_need_attention": len(incomplete_meetings),
            "open_review_items": len(review_items),
            "open_commitments": len(enriched_open),
            "in_progress_commitments": sum(1 for row in enriched_open if row.get("status") == "in_progress"),
            "overdue_commitments": len(overdue_commitments),
            "closed_commitments": len(enriched_closed),
            "active_risks": len(enriched_risks),
            "high_risks": len(high_risks),
            "closed_risks": len(closed_risks),
            "decisions": len(decisions),
            "assignments": len(assignments),
            "people_profiles": len(person_ids),
            "organization_profiles": organization_count,
            "topic_profiles": len(topic_ids),
            "latest_effectiveness_score": latest_score,
            "effectiveness_score_delta": score_delta,
            "stalled_items": stalled["counts"]["total"],
            "stalled_topics": stalled["counts"]["topics"],
            "rescheduled_commitments": stalled["counts"]["rescheduled_commitments"],
            "unanswered_promises": stalled["counts"]["unanswered_promises"],
            "workspace_schema_version": workspace.get("schema_version"),
            "workspace_needs_migration": workspace.get("needs_migration", False),
            "data_errors": issue_counts["error"],
            "data_warnings": issue_counts["warning"],
            "data_info": issue_counts["info"],
        },
        "meetings": meetings,
        "participation": participation,
        "commitments": {"open": enriched_open, "closed": enriched_closed},
        "risks": {"active": enriched_risks, "closed": closed_risks},
        "decisions": decisions,
        "assignments": assignments,
        "issues": issues,
        "capabilities": capabilities,
        "review_queue": review_queue,
        "stalled": stalled,
        "workspace": workspace,
        "next_actions": {"overdue_commitments": overdue_commitments, "high_risks": high_risks, "incomplete_meetings": incomplete_meetings, "open_review_items": review_items},
    }


def stalled_state(root: Path, as_of: date, issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Зависшие вопросы (stalled.py) с пометками очереди решений; сбой чтения — в целостность данных, а не падение."""
    from .stalled import find_stalled, promise_question, reschedule_question, topic_question
    from .review import item as queue_item, latest_decisions

    try:
        stalled = find_stalled(root, as_of)
    except NaError as exc:
        add_issue(issues, "warning", "stalled_not_computed", f"Зависшие вопросы не рассчитаны: {exc}")
        return {"rules": {}, "topics": [], "rescheduled_commitments": [], "unanswered_promises": [], "counts": {"topics": 0, "rescheduled_commitments": 0, "unanswered_promises": 0, "total": 0}}
    decisions = latest_decisions(root)

    def attach(row: dict[str, Any], category: str, meeting_id: Any, question: str, source_path: str, source_ref: str) -> None:
        item_id = queue_item(category, meeting_id, question, source_path, source_ref)["item_id"]
        decision = decisions.get(item_id) or {}
        row["queue_item_id"] = item_id
        row["queue_status"] = "closed" if decision.get("status") in {"closed", "resolved", "dismissed"} else decision.get("status") or "open"
        row["queue_answer"] = decision.get("answer")

    for row in stalled["topics"]:
        attach(row, "stalled_topic", row["last_meeting_id"], topic_question(row), row["meetings"][-1]["signals"][-1]["source_path"], f"topic:{row['key']}")
    for row in stalled["rescheduled_commitments"]:
        attach(row, "commitment_rescheduled", row["meeting_id"], reschedule_question(row), "knowledge/commitments/open.jsonl", str(row["id"]))
    for row in stalled["unanswered_promises"]:
        attach(row, "unanswered_promise", row["meeting_id"], promise_question(row), "knowledge/commitments/open.jsonl", str(row["id"]))
    return stalled


def workspace_state(root: Path) -> dict[str, Any]:
    from .workspace_schema import describe

    return describe(root)


def workspace_text(workspace: dict[str, Any]) -> str:
    if workspace.get("state") == "current":
        return f"{workspace['schema_version']} (актуальная)"
    return workspace.get("message") or "не определена"


def _quote_ref(meeting_id: Any, timestamp: Any, quote: Any) -> str:
    parts = [f"[[{meeting_id}]]"] if meeting_id else []
    if timestamp:
        parts.append(f"`{timestamp}`")
    if quote:
        parts.append(f"«{quote}»")
    return " ".join(parts) or "источник не указан"


def stalled_markdown(stalled: dict[str, Any]) -> list[str]:
    lines = [
        "### Зависшие вопросы",
        "",
        "_Не путать с просрочкой: просрочка — срок прошёл; зависание — повтор без движения "
        "(тема без решения на нескольких встречах, срок переносили, обещание напоминали), даже если новый срок ещё не наступил._",
        "",
    ]
    if not stalled["counts"]["total"]:
        rules = stalled.get("rules") or {}
        lines.append("- Зависших вопросов нет" + (f" (правила: {'; '.join(rules.values())})." if rules else "."))
        return lines
    if stalled["topics"]:
        lines += ["**Темы без решения в реестре:**", ""]
        for row in stalled["topics"]:
            dates = ", ".join(meeting["date"] or meeting["meeting_id"] for meeting in row["meetings"])
            decisions = "; ".join(f"`{item['id']}` {item['status']}" + (f" (пересмотр {item['review_date']})" if item.get("review_date") else "") for item in row["register_decisions"]) or "нет"
            days = f", висит {row['days_open']} дн." if row.get("days_open") is not None else ""
            questions = f" — открытые вопросы: {', '.join('«' + text + '»' for text in row['questions'])}" if row.get("questions") else ""
            lines.append(f"- **{row['title']}**" + (f" (`{row['topic_id']}`)" if row.get("topic_id") else "") + f"{questions}; встреч: {row['meetings_count']} ({dates}){days}; решения в реестре: {decisions}; очередь: `{row['queue_item_id']}` ({row['queue_status']}).")
            for meeting in row["meetings"]:
                for signal in meeting["signals"]:
                    lines.append(f"  - {meeting['date'] or '—'}: {signal['text']} — {_quote_ref(meeting['meeting_id'], signal.get('timestamp'), signal.get('quote'))}")
        lines.append("")
    if stalled["rescheduled_commitments"]:
        lines += ["**Сроки, перенесённые повторно:**", ""]
        for row in stalled["rescheduled_commitments"]:
            overdue = "просрочено" if row["is_overdue"] else "текущий срок не наступил"
            lines.append(f"- **{row['owner_name']}** — {row['what']}; переносов: {row['reschedules']} ({' → '.join(row['chain'])}), основание: {row['basis']}; {overdue}; очередь: `{row['queue_item_id']}` ({row['queue_status']}).")
            lines.append(f"  - обязательство: {_quote_ref(row.get('meeting_id'), row.get('source_timestamp'), row.get('source_quote'))}")
            for change in row["changes"]:
                if change.get("recorded_at") or change.get("event_id"):
                    where = f"запись в базе {(change.get('recorded_at') or '')[:10] or 'без даты'}" + (f" по итогам [[{change['meeting_id']}]]" if change.get("meeting_id") else f", событие `{change.get('event_id')}`")
                else:
                    where = f"встреча {change.get('date') or 'без даты'} [[{change.get('meeting_id')}]]"
                reason = f"; причина: {change['change_reason']}" if change.get("change_reason") else ""
                quote = f"; {_quote_ref(None, change.get('timestamp'), change.get('quote'))}" if change.get("quote") else ""
                lines.append(f"  - {change['from']} → {change['to']} ({where}){reason}{quote}")
        lines.append("")
    if stalled["unanswered_promises"]:
        lines += ["**Обещания другой стороны без результата после напоминаний:**", ""]
        for row in stalled["unanswered_promises"]:
            overdue = "; просрочено" if row["is_overdue"] else ""
            lines.append(f"- **{row['owner_name']}** — {row['what']}; срок {row.get('deadline') or 'не установлен'}{overdue}; поднималось на встречах: {row['reminder_meetings']}; очередь: `{row['queue_item_id']}` ({row['queue_status']}).")
            lines.append(f"  - обещание: {_quote_ref(row.get('meeting_id'), row.get('source_timestamp'), row.get('source_quote'))}")
            for reminder in row["reminders"]:
                for episode in reminder["episodes"]:
                    lines.append(f"  - {episode.get('date') or '—'}: {episode['kind']} — {_quote_ref(reminder['meeting_id'], episode.get('timestamp'), episode.get('quote'))}")
        lines.append("")
    return lines


def build_markdown(state: dict[str, Any]) -> str:
    summary = state["summary"]
    profile = state.get("profile") or {}
    profile_text = ", ".join(f"{key}: {value}" for key, value in profile.items()) if profile else "не задан (базовый)"
    lines = [
        "# Контрольный центр системы анализа переговоров",
        "",
        f"**Состояние на:** {state['as_of_date']}  ",
        f"**Сформировано:** {state['generated_at']}  ",
        f"**Версия онтологии:** `{state.get('ontology_version') or 'не определена'}`  ",
        f"**Профиль:** {profile_text}  ",
        f"**Версия схемы базы:** {workspace_text(state.get('workspace') or {})}",
        "",
        "## Главное",
        "",
        "| Показатель | Значение |",
        "|---|---:|",
        table_row(["Обработано встреч", summary["meetings_total"]]),
        table_row(["Полностью готовы", summary["meetings_ready"]]),
        table_row(["Открытые обязательства", summary["open_commitments"]]),
        table_row(["Из них просрочены", summary["overdue_commitments"]]),
        table_row(["Зависшие вопросы", summary.get("stalled_items", 0)]),
        table_row(["Активные риски", summary["active_risks"]]),
        table_row(["Риски высокой важности", summary["high_risks"]]),
        table_row(["Решения в реестре", summary["decisions"]]),
        table_row(["Незакрытые вопросы встреч", summary["open_review_items"]]),
        table_row(["Последняя оценка эффективности", summary["latest_effectiveness_score"]]),
        "",
        "## Системные возможности",
        "",
        "| Компонент | Статус | Детали |",
        "|---|---|---|",
        *[table_row([item["id"], item["status"], item["detail"]]) for item in state.get("capabilities", [])],
        "",
        "## Что требует внимания",
        "",
        "### Просроченные обязательства",
        "",
    ]
    overdue = state["next_actions"]["overdue_commitments"]
    if overdue:
        for row in overdue:
            lines.append(f"- **{row['owner_name']}** — {row.get('what')}; исходный срок {row.get('deadline') or 'не установлен'}, просрочка {row['days_overdue']} дн., контрольная дата {row.get('control_date') or 'не установлена'}.")
    else:
        lines.append("- Просроченных обязательств нет.")
    if state.get("stalled"):
        lines += [""] + stalled_markdown(state["stalled"])
    lines += ["", "### Активные риски высокой важности", ""]
    high_risks = state["next_actions"]["high_risks"]
    if high_risks:
        for row in high_risks:
            lines.append(f"- **{row.get('id')}** — {row.get('description')} Владелец: {row['owner_name']}; срок: {row.get('mitigation_deadline') or 'не установлен'}.")
    else:
        lines.append("- Активных рисков высокой важности нет.")
    incomplete = state["next_actions"]["incomplete_meetings"]
    lines += ["", "### Незавершённые встречи", ""]
    if incomplete:
        for row in incomplete:
            resume = f"; продолжить с `{row['resume_from']}`" if row.get("resume_from") else ""
            lines.append(f"- [[{row['meeting_id']}]] — {STATUS_LABELS.get(row['pipeline_status'], row['pipeline_status'])}{resume}.")
    elif summary["meetings_total"] == 0:
        lines.append("- Встреч в базе пока нет.")
    else:
        lines.append("- Все встречи прошли обязательный конвейер.")
    review_items = state["next_actions"]["open_review_items"]
    lines += ["", "### Незакрытые вопросы встреч", ""]
    if review_items:
        for row in review_items:
            suffix = "" if str(row["item"]).endswith((".", "!", "?")) else "."
            lines.append(f"- [[{row['meeting_id'] or 'база'}]] `{row['item_id']}` — {row['item']}{suffix}")
    else:
        lines.append("- Незакрытых вопросов нет.")

    lines += ["", "## Встречи", "", "| Дата | Встреча | Шлюз | Результат | Эффективность | Анализ | Аудио | Состояние |", "|---|---|---|---|---:|---:|---|---|"]
    for row in reversed(state["meetings"]):
        lines.append(table_row([row.get("date"), f"[[{row['meeting_id']}]]", row.get("gate_consent"), row.get("outcome"), row.get("effectiveness_score"), f"{row['analysis_files']}/{row['analysis_files_required']}", "да" if row["audio_available"] else "нет", STATUS_LABELS.get(row["pipeline_status"], row["pipeline_status"])]))

    lines += ["", "## Открытые обязательства", "", "| ID | Ответственный | Действие | Срок | Контрольная дата | Статус |", "|---|---|---|---|---|---|"]
    for row in sorted(state["commitments"]["open"], key=lambda item: (not item["is_overdue"], item.get("deadline") or "9999-12-31")):
        lines.append(table_row([row.get("id"), row["owner_name"], row.get("what"), row.get("deadline"), row.get("control_date"), row.get("status")]))
    if not state["commitments"]["open"]:
        lines.append(table_row(["—", "—", "Открытых обязательств нет", "—", "—", "—"]))

    lines += ["", "## Активные риски", "", "| ID | Важность | Риск | Владелец | Срок | Статус |", "|---|---|---|---|---|---|"]
    for row in state["risks"]["active"]:
        lines.append(table_row([row.get("id"), row.get("severity"), row.get("description"), row.get("owner_name"), row.get("mitigation_deadline"), row.get("status")]))
    if not state["risks"]["active"]:
        lines.append(table_row(["—", "—", "Активных рисков нет", "—", "—", "—"]))

    lines += ["", "## Решения и поручения", "", "| Тип | ID | Формулировка | Статус | Владелец |", "|---|---|---|---|---|"]
    for row in state.get("decisions", []):
        lines.append(table_row(["решение", row.get("id"), row.get("statement"), row.get("status"), row.get("decision_owner")]))
    for row in state.get("assignments", []):
        lines.append(table_row(["поручение", row.get("id"), row.get("statement"), row.get("status"), row.get("assignee")]))
    if not state.get("decisions") and not state.get("assignments"):
        lines.append(table_row(["—", "—", "Записей нет", "—", "—"]))

    lines += ["", "## Целостность данных", "", f"- Ошибок: **{summary['data_errors']}**", f"- Предупреждений: **{summary['data_warnings']}**", f"- Информационных пробелов: **{summary['data_info']}**", ""]
    for issue in state["issues"]:
        if issue["severity"] in {"error", "warning"}:
            suffix = f" (`{issue['path']}`)" if issue.get("path") else ""
            entity = f" [{issue['entity_id']}]" if issue.get("entity_id") else ""
            lines.append(f"- `{issue['severity']}` `{issue['code']}`{entity}: {issue['message']}{suffix}")
    if not any(issue["severity"] in {"error", "warning"} for issue in state["issues"]):
        lines.append("- Ошибок и предупреждений нет.")
    lines += ["", "---", "", "_Это производное представление. Источниками истины остаются negotiations/ и knowledge/._", ""]
    return "\n".join(lines)


def validate_control_center(root: Path) -> dict[str, Any]:
    """Согласованность control_center.json и control_center.md (без SQLite-панели)."""
    state = read_json_or_none(root / "system/control_center.json")
    text = (root / "system/control_center.md").read_text(encoding="utf-8-sig") if (root / "system/control_center.md").is_file() else ""
    errors: list[str] = []
    if not isinstance(state, dict):
        errors.append("control_center.json отсутствует или некорректен")
    if not text:
        errors.append("control_center.md отсутствует")
    if isinstance(state, dict):
        for row in state.get("commitments", {}).get("open", []):
            if row.get("what") and row["what"] not in text:
                errors.append(f"обязательство {row.get('id')} отсутствует в markdown")
        for row in state.get("risks", {}).get("active", []):
            if row.get("description") and row["description"] not in text:
                errors.append(f"риск {row.get('id')} отсутствует в markdown")
        for row in state.get("meetings", []):
            if f"[[{row['meeting_id']}]]" not in text:
                errors.append(f"встреча {row['meeting_id']} отсутствует в markdown")
        stalled = state.get("stalled") or {}
        for row in stalled.get("topics", []):
            if row.get("title") and row["title"] not in text:
                errors.append(f"зависшая тема {row.get('key')} отсутствует в markdown")
        for key in ("rescheduled_commitments", "unanswered_promises"):
            for row in stalled.get(key, []):
                if row.get("what") and row["what"] not in text:
                    errors.append(f"зависший пункт {row.get('id')} отсутствует в markdown")
    result = {"valid": not errors, "generated_at": state.get("generated_at") if isinstance(state, dict) else None, "errors": errors}
    write_json(root / "system/control_center_validation.json", result)
    return result


def run_control_center(root: Path, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    output_dir = root / "system"
    output_dir.mkdir(parents=True, exist_ok=True)
    state = build_project_state(root, as_of)
    write_json(output_dir / "control_center.json", state)
    write_text(output_dir / "control_center.md", build_markdown(state))
    validation = validate_control_center(root)
    return {
        "valid": state["summary"]["data_errors"] == 0 and validation["valid"],
        "summary": state["summary"],
        "outputs": ["system/control_center.json", "system/control_center.md"],
        "validation": validation,
    }
