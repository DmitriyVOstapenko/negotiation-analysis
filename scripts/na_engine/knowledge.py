"""Предложение по базе знаний: показ diff без изменений и подтверждённое применение.

Инварианты:
- ничего не меняется без `apply --confirm`, а apply требует ранее показанного diff
  того же предложения (по SHA-256 proposal);
- строки с пометкой `(m)` текущего файла обязаны сохраниться в новом содержимом;
- закрытие записи переносит её в закрытый реестр, не удаляя историю;
- повторный apply того же предложения ничего не дублирует;
- apply останавливается, если целевой файл или реестр изменился после показа diff
  (другой чат, ручная правка): снимок хешей целей пишется рядом с diff;
- в ledger попадают снимки обязательств и рисков по knowledge_event.schema.json.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .common import (
    MANUAL_MARK,
    PAIRED_REGISTRIES,
    PROTECTED_KNOWLEDGE_FILES,
    SINGLE_REGISTRIES,
    TERMINAL_STATUSES,
    NaError,
    ensure_workspace,
    meeting_dir,
    now_iso,
    read_json,
    read_json_or_none,
    read_jsonl,
    read_text,
    sha256_file,
    write_json,
    write_jsonl,
    write_text,
)
from .ledger import sync_ledger


PROPOSAL_FILE = "knowledge_proposal.json"
DIFF_FILE = "knowledge_diff.md"
RECEIPT_FILE = "knowledge_apply.json"
SNAPSHOT_FILE = "knowledge_diff_snapshot.json"
VALID_FILE_ACTIONS = {"create", "update"}
VALID_RECORD_ACTIONS = {"append", "update", "close"}
SUMMARY_KEYS = ("what", "description", "statement", "title", "decision", "id")


# ---------------------------------------------------------------------------
# Загрузка и проверка предложения
# ---------------------------------------------------------------------------


def _safe_relative(raw: str) -> str:
    path = str(raw).replace("\\", "/").strip()
    if path.startswith("./"):
        path = path[2:]
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if not parts or parts[0] != "knowledge" or any(part == ".." for part in parts):
        raise NaError(f"Путь вне knowledge/ недопустим: {raw!r}")
    normalized = "/".join(parts)
    if normalized in PROTECTED_KNOWLEDGE_FILES or normalized.startswith("knowledge/events/"):
        raise NaError(f"Файл защищён от изменений через предложение: {normalized}")
    return normalized


def _legacy_alias(root: Path, relative_path: str) -> Path | None:
    """organizations/ — основной каталог; существующий файл в companies/ читается как текущее состояние."""
    if relative_path.startswith("knowledge/organizations/"):
        alias = root / relative_path.replace("knowledge/organizations/", "knowledge/companies/", 1)
        if alias.is_file():
            return alias
    return None


def _current_file(root: Path, relative_path: str) -> tuple[Path | None, str]:
    target = root / relative_path
    if target.is_file():
        return target, read_text(target)
    alias = _legacy_alias(root, relative_path)
    if alias is not None:
        return alias, read_text(alias)
    return None, ""


def manual_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if MANUAL_MARK in line]


def load_proposal(meeting_path: Path) -> tuple[dict[str, Any], str]:
    path = meeting_path / PROPOSAL_FILE
    if not path.is_file():
        raise NaError(f"Нет {PROPOSAL_FILE} в {meeting_path.name}: предложение готовит LLM-этап обновления базы")
    proposal = read_json(path)
    if not isinstance(proposal, dict):
        raise NaError(f"{PROPOSAL_FILE}: корень должен быть объектом")
    if proposal.get("meeting_id") != meeting_path.name:
        raise NaError(f"{PROPOSAL_FILE}: meeting_id {proposal.get('meeting_id')!r} не совпадает с каталогом {meeting_path.name!r}")
    for key in ("files", "records"):
        if key in proposal and not isinstance(proposal[key], list):
            raise NaError(f"{PROPOSAL_FILE}: поле {key} должно быть списком")
    return proposal, sha256_file(path) or ""


def _summary(record: dict[str, Any]) -> str:
    for key in SUMMARY_KEYS:
        if record.get(key):
            return str(record[key])
    return "—"


def _registry_records(root: Path, relative_path: str) -> list[dict[str, Any]]:
    return read_jsonl(root / relative_path)


def _paired(relative_path: str) -> tuple[str | None, str | None]:
    """(открытый, закрытый) для любого из двух путей пары; (None, None) для одиночного реестра."""
    if relative_path in PAIRED_REGISTRIES:
        return relative_path, PAIRED_REGISTRIES[relative_path]
    for open_path, closed_path in PAIRED_REGISTRIES.items():
        if relative_path == closed_path:
            return open_path, closed_path
    return None, None


def plan_changes(root: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    """Рассчитать изменения без записи. Ошибки предусловий собираются списком."""
    errors: list[str] = []
    file_plans: list[dict[str, Any]] = []
    record_plans: list[dict[str, Any]] = []

    for index, entry in enumerate(proposal.get("files") or []):
        if not isinstance(entry, dict):
            errors.append(f"files[{index}]: ожидается объект")
            continue
        try:
            relative_path = _safe_relative(entry.get("path") or "")
        except NaError as exc:
            errors.append(f"files[{index}]: {exc}")
            continue
        action = entry.get("action")
        content = entry.get("content")
        if action not in VALID_FILE_ACTIONS:
            errors.append(f"files[{index}] {relative_path}: action должен быть create или update")
            continue
        if not isinstance(content, str) or not content.strip():
            errors.append(f"files[{index}] {relative_path}: content должен быть непустой строкой")
            continue
        if not relative_path.endswith(".md"):
            errors.append(f"files[{index}] {relative_path}: через files изменяются только Markdown-файлы; реестры — через records")
            continue
        current_path, current_text = _current_file(root, relative_path)
        if action == "create" and current_path is not None:
            errors.append(f"files[{index}] {relative_path}: файл уже существует ({current_path.name}); используйте action=update")
            continue
        if action == "update" and current_path is None:
            errors.append(f"files[{index}] {relative_path}: файл не существует; используйте action=create")
            continue
        lost = [line for line in manual_lines(current_text) if line not in content.splitlines()]
        if lost:
            errors.append(
                f"files[{index}] {relative_path}: обновление теряет ручные строки с пометкой {MANUAL_MARK}: "
                + "; ".join(line.strip()[:80] for line in lost)
            )
            continue
        if not content.endswith("\n"):
            content += "\n"
        unchanged = current_text == content
        diff = "".join(
            difflib.unified_diff(
                current_text.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relative_path}" if current_path else "/dev/null",
                tofile=f"b/{relative_path}",
            )
        )
        file_plans.append({
            "path": relative_path,
            "action": action,
            "current_path": str(current_path.relative_to(root).as_posix()) if current_path else None,
            "unchanged": unchanged,
            "manual_lines_preserved": len(manual_lines(current_text)),
            "content": content,
            "diff": diff,
        })

    # Записи реестров: проверяем против актуального состояния с учётом предыдущих записей того же предложения
    staged: dict[str, list[dict[str, Any]]] = {}

    def state(relative_path: str) -> list[dict[str, Any]]:
        if relative_path not in staged:
            staged[relative_path] = [dict(item) for item in _registry_records(root, relative_path)]
        return staged[relative_path]

    for index, entry in enumerate(proposal.get("records") or []):
        if not isinstance(entry, dict):
            errors.append(f"records[{index}]: ожидается объект")
            continue
        try:
            registry_path = _safe_relative(entry.get("registry") or "")
        except NaError as exc:
            errors.append(f"records[{index}]: {exc}")
            continue
        if not registry_path.endswith(".jsonl"):
            errors.append(f"records[{index}] {registry_path}: реестр должен быть JSONL")
            continue
        action = entry.get("action")
        record_id = entry.get("id") or (entry.get("record") or {}).get("id")
        record = entry.get("record") if isinstance(entry.get("record"), dict) else {}
        if action not in VALID_RECORD_ACTIONS:
            errors.append(f"records[{index}] {registry_path}: action должен быть append, update или close")
            continue
        if not record_id:
            errors.append(f"records[{index}] {registry_path}: не указан id записи")
            continue
        record_id = str(record_id)
        open_path, closed_path = _paired(registry_path)
        terminal = TERMINAL_STATUSES.get(open_path or registry_path, set())
        current = state(registry_path)
        existing = next((item for item in current if str(item.get("id")) == record_id), None)
        sibling_existing = None
        if open_path and closed_path:
            other = closed_path if registry_path == open_path else open_path
            sibling_existing = next((item for item in state(other) if str(item.get("id")) == record_id), None)

        if action == "append":
            if existing is not None or sibling_existing is not None:
                errors.append(f"records[{index}] {registry_path}: id {record_id!r} уже существует; используйте update или close")
                continue
            if not record:
                errors.append(f"records[{index}] {registry_path}: для append нужен record")
                continue
            new_record = dict(record, id=record_id)
            if registry_path == open_path and new_record.get("status") in terminal:
                errors.append(f"records[{index}] {registry_path}: запись с терминальным статусом {new_record.get('status')!r} не добавляется в открытый реестр")
                continue
            current.append(new_record)
            record_plans.append({"registry": registry_path, "action": "append", "id": record_id, "summary": _summary(new_record), "changes": "новая запись", "record": new_record})
        elif action == "update":
            if existing is None:
                errors.append(f"records[{index}] {registry_path}: id {record_id!r} не найден; используйте append")
                continue
            merged = {**existing, **record, "id": record_id}
            if registry_path == open_path and merged.get("status") in terminal and existing.get("status") not in terminal:
                errors.append(f"records[{index}] {registry_path}: перевод в статус {merged.get('status')!r} выполняется только через action=close (решение пользователя)")
                continue
            changes = [f"{key}: {existing.get(key)!r} → {merged.get(key)!r}" for key in merged if existing.get(key) != merged.get(key)]
            if not changes:
                record_plans.append({"registry": registry_path, "action": "update", "id": record_id, "summary": _summary(merged), "changes": "без изменений", "record": merged, "unchanged": True})
                continue
            current[current.index(existing)] = merged
            record_plans.append({"registry": registry_path, "action": "update", "id": record_id, "summary": _summary(merged), "changes": "; ".join(changes), "record": merged})
        else:  # close
            if open_path and closed_path:
                if registry_path != open_path:
                    errors.append(f"records[{index}] {registry_path}: close указывается для открытого реестра {open_path}")
                    continue
                if existing is None:
                    if sibling_existing is not None:
                        record_plans.append({"registry": registry_path, "action": "close", "id": record_id, "summary": _summary(sibling_existing), "changes": "уже закрыто", "record": sibling_existing, "unchanged": True})
                        continue
                    errors.append(f"records[{index}] {registry_path}: id {record_id!r} не найден в открытом реестре")
                    continue
                default_status = "done" if "commitments" in registry_path else "closed"
                closed_record = {**existing, **record, "id": record_id}
                if record.get("status") is None:
                    closed_record["status"] = existing["status"] if existing.get("status") in terminal else default_status
                if closed_record.get("status") not in terminal:
                    errors.append(f"records[{index}] {registry_path}: статус закрытия должен быть одним из {sorted(terminal)}")
                    continue
                closed_record["closed_at"] = closed_record.get("closed_at") or now_iso()
                closed_record["closed_source"] = "user_confirmation"
                closed_record["closed_in_meeting_id"] = proposal.get("meeting_id")
                current.remove(existing)
                state(closed_path).append(closed_record)
                record_plans.append({"registry": registry_path, "action": "close", "id": record_id, "summary": _summary(closed_record), "changes": f"перенос в {closed_path}; status: {existing.get('status')!r} → {closed_record['status']!r}", "record": closed_record, "target_registry": closed_path})
            else:
                if existing is None:
                    errors.append(f"records[{index}] {registry_path}: id {record_id!r} не найден")
                    continue
                merged = {**existing, **record, "id": record_id}
                if merged.get("status") not in terminal:
                    errors.append(f"records[{index}] {registry_path}: для close нужен терминальный статус из {sorted(terminal)}")
                    continue
                merged["closed_at"] = merged.get("closed_at") or now_iso()
                merged["closed_source"] = "user_confirmation"
                current[current.index(existing)] = merged
                record_plans.append({"registry": registry_path, "action": "close", "id": record_id, "summary": _summary(merged), "changes": f"status: {existing.get('status')!r} → {merged['status']!r}", "record": merged})

    known_registries = set(SINGLE_REGISTRIES) | set(PAIRED_REGISTRIES) | set(PAIRED_REGISTRIES.values())
    nonstandard = sorted(path for path in staged if path not in known_registries)
    return {"errors": errors, "files": file_plans, "records": record_plans, "registries": staged, "nonstandard_registries": nonstandard}


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def render_diff(meeting_id: str, proposal_sha: str, plan: dict[str, Any], proposal: dict[str, Any]) -> str:
    files = plan["files"]
    records = plan["records"]
    effective_files = [item for item in files if not item["unchanged"]]
    effective_records = [item for item in records if not item.get("unchanged")]
    lines = [
        f"# Предлагаемое обновление базы знаний: {meeting_id}",
        "",
        f"**Предложение:** `{PROPOSAL_FILE}` sha256 `{proposal_sha}`  ",
        f"**Сформировано:** {now_iso()}  ",
        f"**Версия контрактов:** `{proposal.get('contract_version') or 'не указана'}`  ",
        "**Статус:** `proposed` — база знаний НЕ изменена. Применение: `na.py knowledge apply --meeting … --confirm`.",
        f"**Снимок целей:** `{SNAPSHOT_FILE}` — если до применения изменится любой из затронутых файлов или реестров, apply остановится.",
        "",
        "## Сводка",
        "",
        f"- Файлов к созданию: {sum(1 for item in effective_files if item['action'] == 'create')}",
        f"- Файлов к обновлению: {sum(1 for item in effective_files if item['action'] == 'update')}",
        f"- Записей реестров: добавить {sum(1 for item in effective_records if item['action'] == 'append')}, "
        f"обновить {sum(1 for item in effective_records if item['action'] == 'update')}, закрыть {sum(1 for item in effective_records if item['action'] == 'close')}",
        f"- Ошибок предусловий: {len(plan['errors'])}",
        "",
    ]
    if plan["errors"]:
        lines += ["## Ошибки предусловий (применение заблокировано)", ""]
        lines += [f"- {item}" for item in plan["errors"]]
        lines.append("")
    if not effective_files and not effective_records and not plan["errors"]:
        lines += ["## Изменений нет", "", "Предложение не вносит новых фактов в базу знаний.", ""]

    if effective_files:
        lines += ["## Файлы", ""]
        for item in effective_files:
            lines += [f"### `{item['path']}` — {item['action']}", ""]
            if item["current_path"] and item["current_path"] != item["path"]:
                lines += [f"_Текущее состояние прочитано из устаревшего расположения `{item['current_path']}`; запись будет выполнена в `{item['path']}`._", ""]
            if item["manual_lines_preserved"]:
                lines += [f"_Сохранено ручных строк `{MANUAL_MARK}`: {item['manual_lines_preserved']}._", ""]
            lines += ["```diff", item["diff"].rstrip("\n"), "```", ""]
    if effective_records:
        lines += ["## Записи реестров", "", "| Реестр | Действие | ID | Запись | Изменения |", "|---|---|---|---|---|"]
        for item in effective_records:
            lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in (item["registry"], item["action"], item["id"], item["summary"], item["changes"])) + " |")
        lines.append("")
    lines += ["---", "", "_Diff показан до записи. Память меняется только после явного подтверждения пользователя._", ""]
    return "\n".join(lines)


def _target_snapshot(root: Path, plan: dict[str, Any]) -> dict[str, str | None]:
    """Хеши всех файлов, которые apply прочитает или перезапишет (None — файла нет)."""
    targets: set[str] = set()
    for item in plan["files"]:
        targets.add(item["path"])
        if item.get("current_path"):
            targets.add(item["current_path"])
    targets.update(plan["registries"].keys())
    return {target: sha256_file(root / target) for target in sorted(targets)}


def run_diff(root: Path, meeting_id: str) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    proposal, proposal_sha = load_proposal(path)
    plan = plan_changes(root, proposal)
    write_text(path / DIFF_FILE, render_diff(meeting_id, proposal_sha, plan, proposal))
    write_json(path / SNAPSHOT_FILE, {"proposal_sha256": proposal_sha, "taken_at": now_iso(), "targets": _target_snapshot(root, plan)})
    return {
        "valid": not plan["errors"],
        "meeting_id": meeting_id,
        "proposal_sha256": proposal_sha,
        "files": [{"path": item["path"], "action": item["action"], "unchanged": item["unchanged"]} for item in plan["files"]],
        "records": [{"registry": item["registry"], "action": item["action"], "id": item["id"], "unchanged": bool(item.get("unchanged"))} for item in plan["records"]],
        "errors": plan["errors"],
        "diff": DIFF_FILE,
        "applied": False,
    }


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _diff_sha(meeting_path: Path) -> str | None:
    text = read_text(meeting_path / DIFF_FILE)
    for line in text.splitlines():
        if "sha256 `" in line:
            return line.split("sha256 `", 1)[1].split("`", 1)[0]
    return None


def run_apply(root: Path, meeting_id: str, confirm: bool) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    if not confirm:
        raise NaError("Отказ: применение предложения требует явного подтверждения пользователя (--confirm). Сначала покажите diff: na.py knowledge diff --meeting …")
    ensure_workspace(root)
    proposal, proposal_sha = load_proposal(path)

    receipt = read_json_or_none(path / RECEIPT_FILE) or {}
    if proposal_sha in receipt.get("applied_proposals", []):
        return {
            "valid": True,
            "meeting_id": meeting_id,
            "status": "already_applied",
            "proposal_sha256": proposal_sha,
            "note": "Предложение с этим хешем уже применено; повторный запуск ничего не дублирует.",
        }
    if _diff_sha(path) != proposal_sha:
        raise NaError("Diff для текущего предложения не показан (knowledge_diff.md отсутствует или относится к другой версии). Выполните knowledge diff, затем apply --confirm.")
    snapshot = read_json_or_none(path / SNAPSHOT_FILE) or {}
    if snapshot.get("proposal_sha256") != proposal_sha:
        raise NaError("Нет снимка целевых файлов для этого diff. Выполните knowledge diff заново, покажите его пользователю, затем apply --confirm.")
    changed = sorted(target for target, digest in (snapshot.get("targets") or {}).items() if sha256_file(root / target) != digest)
    if changed:
        raise NaError(
            "Применение остановлено: после показа diff изменились файлы базы — "
            + ", ".join(changed)
            + ". Ничего не записано. Выполните knowledge diff заново и покажите пользователю новую версию.",
            exit_code=1,
        )

    plan = plan_changes(root, proposal)
    if plan["errors"]:
        raise NaError("Применение заблокировано ошибками предусловий:\n" + "\n".join(f"  - {item}" for item in plan["errors"]), exit_code=1)

    written_files: list[str] = []
    for item in plan["files"]:
        if item["unchanged"]:
            continue
        write_text(root / item["path"], item["content"])
        written_files.append(item["path"])
    touched_registries: list[str] = []
    for registry_path, records in plan["registries"].items():
        before = read_jsonl(root / registry_path)
        if before != records:
            write_jsonl(root / registry_path, records)
            touched_registries.append(registry_path)

    ledger = sync_ledger(root)
    applied_at = now_iso()
    record_summary = [{"registry": item["registry"], "action": item["action"], "id": item["id"], "target_registry": item.get("target_registry")} for item in plan["records"] if not item.get("unchanged")]
    receipt = {
        "receipt_version": "1.0.0",
        "meeting_id": meeting_id,
        "applied_proposals": [*receipt.get("applied_proposals", []), proposal_sha],
        "last_applied_at": applied_at,
        "history": [
            *receipt.get("history", []),
            {
                "proposal_sha256": proposal_sha,
                "applied_at": applied_at,
                "confirmation": "user_confirmation",
                "files_written": written_files,
                "registries_written": touched_registries,
                "records": record_summary,
                "ledger_events_appended": ledger["events_appended"],
                "ledger_event_ids": ledger["event_ids"],
            },
        ],
    }
    write_json(path / RECEIPT_FILE, receipt)
    diff_text = read_text(path / DIFF_FILE)
    diff_text = diff_text.replace("**Статус:** `proposed` — база знаний НЕ изменена.", f"**Статус:** `applied` — применено {applied_at} по подтверждению пользователя.", 1)
    diff_text += f"\n## Применено\n\n- Время: {applied_at}\n- Подтверждение: user_confirmation\n- Файлов записано: {len(written_files)}\n- Реестров изменено: {len(touched_registries)}\n- Событий журнала добавлено: {ledger['events_appended']}\n"
    write_text(path / DIFF_FILE, diff_text)
    refresh_index(root)
    return {
        "valid": True,
        "meeting_id": meeting_id,
        "status": "applied",
        "proposal_sha256": proposal_sha,
        "files_written": written_files,
        "registries_written": touched_registries,
        "records": record_summary,
        "ledger_events_appended": ledger["events_appended"],
    }


# ---------------------------------------------------------------------------
# knowledge/index.md — навигационный индекс (пересобирается, если создан движком)
# ---------------------------------------------------------------------------

INDEX_MARK = "<!-- generated by na.py: ручные правки сюда не вносите, индекс пересобирается -->"


def _headings(directory: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if not directory.is_dir():
        return rows
    for file in sorted(directory.glob("*.md")):
        if file.name.casefold() == "readme.md":
            continue
        heading = file.stem
        for line in read_text(file).splitlines():
            if line.startswith("# "):
                heading = line[2:].strip()
                break
        rows.append((file.stem, heading))
    return rows


def refresh_index(root: Path) -> bool:
    index_path = root / "knowledge" / "index.md"
    if index_path.is_file() and INDEX_MARK not in read_text(index_path):
        return False
    knowledge = root / "knowledge"
    sections = [
        ("Люди", "people"),
        ("Организации", "organizations"),
        ("Организации (устаревший каталог companies/)", "companies"),
        ("Темы", "topics"),
        ("Наблюдённые тактики", "tactics_observed"),
    ]
    lines = ["# Индекс базы знаний", "", INDEX_MARK, "", f"Обновлено: {now_iso()}", ""]
    for title, folder in sections:
        rows = _headings(knowledge / folder)
        if not rows and folder == "companies":
            continue
        lines += [f"## {title} ({len(rows)})", ""]
        lines += [f"- [[{stem}]] — {heading}" for stem, heading in rows] or ["- пусто"]
        lines.append("")
    counts = {
        "обязательства открытые": len(read_jsonl(knowledge / "commitments/open.jsonl")),
        "обязательства закрытые": len(read_jsonl(knowledge / "commitments/closed.jsonl")),
        "риски активные": len(read_jsonl(knowledge / "risks/active.jsonl")),
        "риски закрытые": len(read_jsonl(knowledge / "risks/closed.jsonl")),
        "решения": len(read_jsonl(knowledge / "decisions/register.jsonl")),
        "поручения": len(read_jsonl(knowledge / "assignments/register.jsonl")),
    }
    lines += ["## Реестры", ""] + [f"- {name}: {value}" for name, value in counts.items()] + [""]
    write_text(index_path, "\n".join(lines))
    return True
