"""Возобновляемый детерминированный конвейер: manifest, refresh, refresh-all, status.

LLM-этапы не запускаются: если 11 JSON или knowledge_proposal.json не готовы,
manifest показывает первый незавершённый этап (resume_from).
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Callable

from . import ENGINE_VERSION
from .analysis_profile import profile_summary, required_analysis_files
from .common import (
    ANALYSIS_CONTRACTS_PATH,
    ANALYSIS_FILES,
    EVENT_SCHEMA_PATH,
    PLUGIN_ROOT,
    NaError,
    canonical_json,
    ensure_workspace,
    gate_state,
    meeting_dir,
    meeting_dirs,
    now_iso,
    read_json_or_none,
    relative,
    sha256_file,
    sha256_text,
    write_json,
)
from .control_center import ontology_version, run_control_center
from .followup import run_followup
from .knowledge import DIFF_FILE, PROPOSAL_FILE, RECEIPT_FILE, refresh_index
from .ledger import project_ledger, sync_ledger, validate_projection
from .report import run_report
from .review import run_queue
from .search import build_index
from .validate import contract_version, run_validate


RUN_MANIFEST_VERSION = "1.1.0"
SOURCE_FILES = ["source_packet.json", "meta.yaml", "transcript.txt", "review.yaml"]
OPTIONAL_SOURCE_FILES = ["note.md", "source_note.md", "note_plaud.md"]
KNOWLEDGE_INPUTS = [
    "knowledge/review_decisions.jsonl",
    "knowledge/commitments/open.jsonl",
    "knowledge/commitments/closed.jsonl",
    "knowledge/risks/active.jsonl",
    "knowledge/risks/closed.jsonl",
    "knowledge/decisions/register.jsonl",
    "knowledge/assignments/register.jsonl",
]
ENGINE_INPUTS = [
    ANALYSIS_CONTRACTS_PATH,
    EVENT_SCHEMA_PATH,
    *sorted((PLUGIN_ROOT / "scripts" / "na_engine").glob("*.py")),
]
FOLLOW_UP_FILES = ("follow_up.json", "follow_up_protocol.md", "follow_up_message.md")


def fingerprint(path: Path, label: str) -> dict[str, Any]:
    return {"path": label, "exists": path.is_file(), "size": path.stat().st_size if path.is_file() else None, "sha256": sha256_file(path)}


def input_fingerprints(root: Path, meeting_path: Path) -> list[dict[str, Any]]:
    items = [fingerprint(meeting_path / name, relative(meeting_path / name, root)) for name in SOURCE_FILES]
    items += [fingerprint(meeting_path / name, relative(meeting_path / name, root)) for name in OPTIONAL_SOURCE_FILES if (meeting_path / name).is_file()]
    items += [fingerprint(meeting_path / "analysis" / name, relative(meeting_path / "analysis" / name, root)) for name in ANALYSIS_FILES]
    items.append(fingerprint(meeting_path / PROPOSAL_FILE, relative(meeting_path / PROPOSAL_FILE, root)))
    items += [fingerprint(root / name, name) for name in KNOWLEDGE_INPUTS]
    items += [fingerprint(path, "plugin:" + relative(path, PLUGIN_ROOT)) for path in ENGINE_INPUTS]
    return items


def combined_digest(items: list[dict[str, Any]]) -> str:
    return sha256_text(canonical_json(items))


def stage(stage_id: str, passed: bool, details: str, blocking: bool = True) -> dict[str, Any]:
    return {"id": stage_id, "status": "completed" if passed else "pending", "blocking": blocking, "details": details}


def knowledge_state(meeting_path: Path) -> tuple[bool, str]:
    diff_exists = (meeting_path / DIFF_FILE).is_file()
    proposal_path = meeting_path / PROPOSAL_FILE
    if not proposal_path.is_file():
        if diff_exists:
            return True, "knowledge_diff.md оформлен без knowledge_proposal.json (прежний формат базы)"
        return False, "knowledge_proposal.json отсутствует: этап обновления базы не начат"
    proposal_sha = sha256_file(proposal_path)
    receipt = read_json_or_none(meeting_path / RECEIPT_FILE) or {}
    if proposal_sha in receipt.get("applied_proposals", []):
        return True, f"предложение {proposal_sha[:12]} применено по подтверждению пользователя"
    if diff_exists:
        text = (meeting_path / DIFF_FILE).read_text(encoding="utf-8-sig")
        if f"sha256 `{proposal_sha}`" in text and "## Изменений нет" in text:
            return True, "предложение не вносит изменений в базу"
        if f"sha256 `{proposal_sha}`" in text:
            return False, "diff показан, ожидается подтверждение: knowledge apply --confirm"
        return False, "diff относится к другой версии предложения: повторите knowledge diff"
    return False, "diff не показан: выполните knowledge diff"


def build_manifest(root: Path, meeting_path: Path) -> dict[str, Any]:
    meeting_id = meeting_path.name
    source_state = {name: (meeting_path / name).is_file() for name in SOURCE_FILES}
    profile = profile_summary(meeting_path)
    analysis_state = {name: (meeting_path / "analysis" / name).is_file() for name in profile["required"]}
    gate = gate_state(meeting_path)
    validation = read_json_or_none(meeting_path / "analysis" / "validation_report.json") or {}
    levels = validation.get("quote_stats") or {}
    quote_levels = f" (буквально {levels.get('exact', 0)}, нормализация {levels.get('normalized', 0)}, «…» {levels.get('ellipsis', 0)})" if levels else ""
    report_validation = read_json_or_none(meeting_path / "analysis" / "report_auto_validation.json") or {}
    follow_up = read_json_or_none(meeting_path / "follow_up.json") or {}
    follow_up_ready = all((meeting_path / name).is_file() for name in FOLLOW_UP_FILES) and follow_up.get("delivery_status") == "draft_not_sent"
    knowledge_done, knowledge_details = knowledge_state(meeting_path)
    speakers = gate["review"].get("speaker_mapping") if isinstance(gate["review"].get("speaker_mapping"), dict) else {}
    receipt = read_json_or_none(meeting_path / RECEIPT_FILE) or {}

    stages = [
        stage("sources_ready", all(source_state.values()), f"исходники: {sum(source_state.values())}/{len(source_state)}"),
        stage("privacy_gate_passed", gate["consent"] == "yes", f"шлюз: consent={gate['consent']}, masking={gate['masking_mode']}, sensitivity={gate['sensitivity']}, routes={gate['allowed_routes']}"),
        stage("speakers_confirmed", speakers.get("status") == "confirmed", "карта спикеров подтверждена" if speakers.get("status") == "confirmed" else "карта спикеров не подтверждена", blocking=False),
        stage("analysis_complete", all(analysis_state.values()), f"аналитические JSON: {sum(analysis_state.values())}/{len(analysis_state)} (профиль {profile['profile']}"
              + (f"; не выполняются по профилю: {len(profile['skipped_by_profile'])}" if profile["skipped_by_profile"] else "") + ")"),
        stage("schema_validated", validation.get("schema_valid") is True, f"JSON Schema {validation.get('schema_version') or contract_version()}: {'пройдена' if validation.get('schema_valid') is True else 'не пройдена'}"),
        stage("quotes_validated", validation.get("valid") is True, f"цитат проверено: {validation.get('checked_quotes', 0)}{quote_levels}; ошибок: {len(validation.get('errors', []))}"),
        stage("auto_report_built", (meeting_path / "report_auto.md").is_file(), "report_auto.md создан" if (meeting_path / "report_auto.md").is_file() else "report_auto.md отсутствует"),
        stage("auto_report_validated", report_validation.get("valid") is True, f"проверка отчёта: {'пройдена' if report_validation.get('valid') is True else 'не пройдена'}"),
        stage("human_report_ready", (meeting_path / "report.md").is_file(), "финальный report.md присутствует" if (meeting_path / "report.md").is_file() else "report.md отсутствует"),
        stage("follow_up_prepared", follow_up_ready, "follow-up пакет подготовлен, статус draft_not_sent" if follow_up_ready else "follow-up комплект неполон"),
        stage("knowledge_applied", knowledge_done, knowledge_details),
    ]
    ready = all(item["status"] == "completed" for item in stages if item["blocking"])
    first_pending = next((item["id"] for item in stages if item["status"] != "completed" and item["blocking"]), None)
    inputs = input_fingerprints(root, meeting_path)
    artifacts = [fingerprint(meeting_path / name, relative(meeting_path / name, root)) for name in ("report.md", "report_auto.md", DIFF_FILE, RECEIPT_FILE, *FOLLOW_UP_FILES, "analysis/validation_report.json", "analysis/report_auto_validation.json")]
    return {
        "run_manifest_version": RUN_MANIFEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "meeting_id": meeting_id,
        "overall_status": "ready" if ready else "awaiting_work",
        "resume_from": first_pending,
        "generated_at": now_iso(),
        "contracts": {
            "analysis_schema_version": contract_version(),
            "analysis_schema_sha256": sha256_file(ANALYSIS_CONTRACTS_PATH),
            "knowledge_event_schema_sha256": sha256_file(EVENT_SCHEMA_PATH),
            "ontology_version": ontology_version(),
        },
        "gate": {key: gate[key] for key in ("consent", "masking_mode", "sensitivity", "allowed_routes", "retention_decision")},
        "analysis_profile": {key: profile[key] for key in ("profile", "required", "skipped_by_profile")},
        "pipeline_input_digest": combined_digest(inputs),
        "source_fingerprints": inputs,
        "artifact_fingerprints": artifacts,
        "knowledge_apply": {"applied_proposals": receipt.get("applied_proposals", []), "last_applied_at": receipt.get("last_applied_at")},
        "stages": stages,
    }


def write_manifest(root: Path, meeting_path: Path) -> dict[str, Any]:
    manifest = build_manifest(root, meeting_path)
    write_json(meeting_path / "run_manifest.json", manifest)
    return manifest


def _action(name: str, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = func()
        ok = bool(result.get("valid", True))
        return {"action": name, "ok": ok, "returncode": 0 if ok else 1, "result": result}
    except NaError as exc:
        return {"action": name, "ok": False, "returncode": exc.exit_code, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — конвейер фиксирует сбой этапа, не прерывая остальные
        return {"action": name, "ok": False, "returncode": 1, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}


def refresh_meeting(root: Path, meeting_path: Path, force: bool = False) -> dict[str, Any]:
    manifest_path = meeting_path / "run_manifest.json"
    current_digest = combined_digest(input_fingerprints(root, meeting_path))
    previous = read_json_or_none(manifest_path)
    required_outputs = [meeting_path / "report_auto.md", meeting_path / "analysis/validation_report.json", meeting_path / "analysis/report_auto_validation.json", *(meeting_path / name for name in FOLLOW_UP_FILES)]
    if not force and previous and previous.get("pipeline_input_digest") == current_digest and previous.get("overall_status") == "ready" and all(path.is_file() for path in required_outputs):
        return {"meeting_id": meeting_path.name, "status": "reused", "reason": "входные fingerprint не изменились", "overall_status": "ready", "resume_from": None, "failed_actions": 0, "actions": []}
    actions: list[dict[str, Any]] = []
    if all((meeting_path / "analysis" / name).is_file() for name in required_analysis_files(meeting_path)):
        actions.append(_action("validate", lambda: run_validate(root, meeting_path.name)))
        if actions[-1]["ok"]:
            actions.append(_action("report", lambda: run_report(root, meeting_path.name)))
        if actions[-1]["ok"]:
            actions.append(_action("followup", lambda: run_followup(root, meeting_path.name)))
    manifest = write_manifest(root, meeting_path)
    failed = [item for item in actions if not item["ok"]]
    return {"meeting_id": meeting_path.name, "status": "refreshed", "overall_status": manifest["overall_status"], "resume_from": manifest["resume_from"], "failed_actions": len(failed), "actions": actions}


def refresh_all(root: Path, force: bool = False) -> dict[str, Any]:
    ensure_workspace(root)
    meetings = [refresh_meeting(root, path, force=force) for path in meeting_dirs(root)]
    review_queue = _action("review_queue", lambda: run_queue(root))
    event_sync = _action("ledger_sync", lambda: sync_ledger(root))
    event_projection = _action("ledger_projection", lambda: project_ledger(root)) if event_sync["ok"] else None
    event_validation = _action("ledger_projection_validation", lambda: validate_projection(root)) if event_projection and event_projection["ok"] else None
    search_index = _action("search_index", lambda: build_index(root))
    index_refreshed = refresh_index(root)
    control = _action("control_center", lambda: run_control_center(root))
    ready = (
        all(item.get("overall_status") == "ready" for item in meetings)
        and review_queue["ok"]
        and event_sync["ok"]
        and bool(event_projection and event_projection["ok"])
        and bool(event_validation and event_validation["ok"])
        and search_index["ok"]
        and control["ok"]
    )
    result = {
        "pipeline_run_version": "1.1.0",
        "engine_version": ENGINE_VERSION,
        "generated_at": now_iso(),
        "valid": ready,
        "meetings": meetings,
        "review_queue": review_queue,
        "event_ledger": {"sync": event_sync, "projection": event_projection, "validation": event_validation},
        "search_index": search_index,
        "knowledge_index_refreshed": index_refreshed,
        "control_center": control,
    }
    write_json(root / "system" / "pipeline_run.json", result)
    return result


def status(root: Path, meeting_id: str | None) -> dict[str, Any]:
    from .workspace_schema import describe as workspace_state  # версия схемы базы: только чтение

    if meeting_id:
        return {**build_manifest(root, meeting_dir(root, meeting_id)), "workspace": workspace_state(root)}
    rows = []
    for path in meeting_dirs(root):
        manifest = build_manifest(root, path)
        rows.append({"meeting_id": path.name, "overall_status": manifest["overall_status"], "resume_from": manifest["resume_from"], "gate": manifest["gate"]["consent"], "analysis_profile": manifest["analysis_profile"]["profile"]})
    run = read_json_or_none(root / "system" / "pipeline_run.json") or {}
    return {"root": str(root), "meetings": rows, "last_refresh_all": {"generated_at": run.get("generated_at"), "valid": run.get("valid")} if run else None, "workspace": workspace_state(root)}


def _workspace_line(workspace: dict[str, Any] | None) -> list[str]:
    if not workspace:
        return []
    if workspace.get("state") in {"needs_migration", "newer_than_plugin", "invalid"}:
        return [f"ВНИМАНИЕ: {workspace['message']}"]
    return [workspace["message"]]


def summary_lines_status(result: dict[str, Any]) -> list[str]:
    if "stages" in result:
        lines = [f"Встреча {result['meeting_id']}: {result['overall_status']}; продолжить с: {result['resume_from'] or '—'}"]
        for item in result["stages"]:
            mark = "+" if item["status"] == "completed" else "-"
            lines.append(f"  [{mark}] {item['id']}: {item['details']}")
        return lines + _workspace_line(result.get("workspace"))
    lines = [f"Рабочий каталог: {result['root']}; встреч: {len(result['meetings'])}"] + _workspace_line(result.get("workspace"))
    for row in result["meetings"]:
        lines.append(f"  {row['meeting_id']}: {row['overall_status']} (шлюз: {row['gate']}; профиль: {row.get('analysis_profile', 'full')}; продолжить с: {row['resume_from'] or '—'})")
    if result.get("last_refresh_all"):
        lines.append(f"Последний refresh-all: {result['last_refresh_all']['generated_at']} valid={result['last_refresh_all']['valid']}")
    return lines
