"""Версия схемы рабочей папки (knowledge/workspace.json) и идемпотентные миграции.

Файл версии — канонический (живёт рядом с базой в knowledge/, а не в производном
system/), поэтому в режиме проекта claude.ai он хранится документом проекта.
Пишет его только движок: при первой записи в пустую папку, при миграции и когда
в базу впервые пишет новая версия плагина. Проект изменений базы его не трогает
(в предложении допустимы только .md-карточки и .jsonl-реестры).

Миграции меняют только то, что описано в их плане; реестры и журнал событий не
трогают. Без --confirm показывается план и ничего не записывается.
"""

from __future__ import annotations

import codecs
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import ENGINE_VERSION
from .common import PLUGIN_ROOT, NaError, meeting_dirs, now_iso, read_json_or_none, relative, write_json


WORKSPACE_FILE = "knowledge/workspace.json"
WORKSPACE_FORMAT = "negotiation-analysis-workspace"
CURRENT_SCHEMA_VERSION = 2
CARD_DIRS = ("knowledge/people", "knowledge/organizations", "knowledge/companies")
NAME_VARIANT_FIELDS = (
    ("speech_variants", "speech_variants: []   # как называют в речи"),
    ("asr_variants", "asr_variants: []      # типовые ошибки распознавания"),
)
SCHEMA_HISTORY = {
    1: "раскладка плагина 0.1–0.5: без файла версии",
    2: "плагин 0.6: файл версии; в карточках людей и организаций есть поля speech_variants и asr_variants",
}


# ---------------------------------------------------------------------------
# Версия плагина и состояние файла
# ---------------------------------------------------------------------------


def plugin_version() -> str | None:
    manifest = read_json_or_none(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return str(version) if version else None


def _version_entry(via: str) -> dict[str, Any]:
    return {"plugin_version": plugin_version(), "engine_version": ENGINE_VERSION, "first_recorded_at": now_iso(), "via": via}


def has_content(root: Path) -> bool:
    """Есть ли в папке данные базы: встречи или непустые файлы в knowledge/ (кроме самого файла версии)."""
    if meeting_dirs(root):
        return True
    knowledge = root / "knowledge"
    if not knowledge.is_dir():
        return False
    for path in knowledge.rglob("*"):
        if path.is_file() and relative(path, root) != WORKSPACE_FILE and path.stat().st_size > 0:
            return True
    return False


def read_workspace(root: Path) -> dict[str, Any]:
    path = root / WORKSPACE_FILE
    if not path.is_file():
        return {"exists": False, "valid": False, "data": None, "error": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"exists": True, "valid": False, "data": None, "error": f"некорректный JSON: {exc}"}
    if not isinstance(data, dict) or not isinstance(data.get("schema_version"), int):
        return {"exists": True, "valid": False, "data": data if isinstance(data, dict) else None, "error": "нет целого поля schema_version"}
    return {"exists": True, "valid": True, "data": data, "error": None}


def describe(root: Path) -> dict[str, Any]:
    """Состояние версии схемы для status и контрольного центра. Только чтение."""
    state = read_workspace(root)
    base = {"file": WORKSPACE_FILE, "current_schema_version": CURRENT_SCHEMA_VERSION, "plugin_version": plugin_version()}
    if state["exists"] and not state["valid"]:
        return {**base, "state": "invalid", "schema_version": None, "pending_migrations": [], "needs_migration": False,
                "message": f"Файл версии {WORKSPACE_FILE} повреждён ({state['error']}). Восстановите его из копии; миграции и запись версии остановлены."}
    if state["valid"]:
        data = state["data"]
        version = data["schema_version"]
        recorded = [entry.get("plugin_version") for entry in data.get("plugin_versions", []) if isinstance(entry, dict)]
        common = {**base, "schema_version": version, "created_at": data.get("created_at"), "origin": data.get("origin"),
                  "plugin_versions": recorded, "migrations_applied": [entry.get("id") for entry in data.get("migrations_applied", []) if isinstance(entry, dict)]}
        if version > CURRENT_SCHEMA_VERSION:
            return {**common, "state": "newer_than_plugin", "pending_migrations": [], "needs_migration": False,
                    "message": f"База записана более новой версией плагина (схема {version} > {CURRENT_SCHEMA_VERSION}). Обновите плагин; этой версией в базу не пишите."}
        pending = [migration.id for migration in MIGRATIONS if migration.to_version > version]
        if pending:
            return {**common, "state": "needs_migration", "pending_migrations": pending, "needs_migration": True,
                    "message": f"Схема базы {version} старше текущей {CURRENT_SCHEMA_VERSION}: нужна миграция — `na.py migrate` (план), затем `na.py migrate --confirm`."}
        return {**common, "state": "current", "pending_migrations": [], "needs_migration": False,
                "message": f"Версия схемы базы {version} — актуальная ({WORKSPACE_FILE})."}
    if has_content(root):
        return {**base, "state": "needs_migration", "schema_version": None, "pending_migrations": [migration.id for migration in MIGRATIONS], "needs_migration": True,
                "message": "Версия схемы базы не зафиксирована: папка создана до 0.6.0. Нужна миграция — `na.py migrate` (план), затем `na.py migrate --confirm`."}
    return {**base, "state": "new", "schema_version": None, "pending_migrations": [], "needs_migration": False,
            "message": "Папка пуста: версия схемы будет зафиксирована при первой записи в базу."}


def _new_document(origin: str, schema_version: int, via: str) -> dict[str, Any]:
    return {
        "workspace_format": WORKSPACE_FORMAT,
        "schema_version": schema_version,
        "origin": origin,
        "created_at": now_iso() if origin == "new_workspace" else None,
        "created_at_note": None if origin == "new_workspace" else "Папка создана до появления файла версии (плагин до 0.6.0); дата создания неизвестна.",
        "version_recorded_at": now_iso(),
        "plugin_versions": [_version_entry(via)],
        "migrations_applied": [],
        "schema_history": {str(key): value for key, value in SCHEMA_HISTORY.items()},
        "note": "Версия схемы рабочей папки. Пишет только движок na.py (первая запись, migrate, запись новой версией плагина); вручную не править.",
    }


def record_workspace_write(root: Path, had_content: bool) -> None:
    """Вызывается из ensure_workspace перед записью в базу. Никогда не прерывает запись.

    Пустая папка — фиксирует текущую схему. Папка с актуальной схемой — дописывает
    версию плагина, если её ещё нет. Старая папка без файла — не трогает: нужна миграция.
    """
    try:
        state = read_workspace(root)
        if not state["exists"]:
            if not had_content:
                write_json(root / WORKSPACE_FILE, _new_document("new_workspace", CURRENT_SCHEMA_VERSION, "new_workspace"))
            return
        if state["valid"] and state["data"]["schema_version"] == CURRENT_SCHEMA_VERSION:
            _note_plugin_version(root, state["data"], "write")
    except (OSError, ValueError):
        return


def _note_plugin_version(root: Path, data: dict[str, Any], via: str) -> bool:
    version = plugin_version()
    known = {entry.get("plugin_version") for entry in data.get("plugin_versions", []) if isinstance(entry, dict)}
    if version in known:
        return False
    data["plugin_versions"] = [*data.get("plugin_versions", []), _version_entry(via)]
    write_json(root / WORKSPACE_FILE, data)
    return True


# ---------------------------------------------------------------------------
# Миграции
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    id: str
    to_version: int
    title: str
    plan: Callable[[Path], dict[str, Any]]
    apply: Callable[[Path, dict[str, Any]], list[str]]


def _plan_record_version(root: Path) -> dict[str, Any]:
    exists = (root / WORKSPACE_FILE).is_file()
    return {
        "changes": [] if exists else [{"path": WORKSPACE_FILE, "action": "create", "details": "файл версии схемы: origin=legacy_folder, дата создания папки неизвестна (null)"}],
        "skipped": [],
    }


def _apply_record_version(root: Path, plan: dict[str, Any]) -> list[str]:
    if (root / WORKSPACE_FILE).is_file():
        return []
    write_json(root / WORKSPACE_FILE, _new_document("legacy_folder", 1, "migrate"))
    return [WORKSPACE_FILE]


def _card_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for folder in CARD_DIRS:
        directory = root / folder
        if directory.is_dir():
            paths.extend(path for path in sorted(directory.glob("*.md")) if path.name.casefold() not in {"readme.md", "index.md"})
    return paths


def _read_card(path: Path) -> tuple[str, bool, str]:
    raw = path.read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, bom, newline


def _with_name_variants(text: str, newline: str) -> tuple[str | None, list[str], str | None]:
    """Вернуть (новый текст, добавленные строки, причина пропуска). Только вставка строк во frontmatter."""
    lines = text.split(newline)
    if not lines or lines[0].strip() != "---":
        return None, [], "нет frontmatter — карточка не изменяется, поля добавляются через проект изменений базы"
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        return None, [], "frontmatter не закрыт строкой --- — карточка не изменяется"
    header = lines[1:closing]
    added = [line for key, line in NAME_VARIANT_FIELDS if not any(re.match(rf"^{key}\s*:", item) for item in header)]
    if not added:
        return None, [], None
    new_lines = lines[:closing] + added + lines[closing:]
    return newline.join(new_lines), added, None


def _plan_name_variants(root: Path) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for path in _card_paths(root):
        text, _bom, newline = _read_card(path)
        new_text, added, reason = _with_name_variants(text, newline)
        if reason:
            skipped.append({"path": relative(path, root), "reason": reason})
        elif new_text is not None:
            changes.append({"path": relative(path, root), "action": "insert_frontmatter_lines", "details": "добавить во frontmatter: " + "; ".join(line.split("#")[0].strip() for line in added), "lines": added})
    return {"changes": changes, "skipped": skipped}


def _apply_name_variants(root: Path, plan: dict[str, Any]) -> list[str]:
    written: list[str] = []
    for change in plan["changes"]:
        path = root / change["path"]
        text, bom, newline = _read_card(path)
        new_text, added, reason = _with_name_variants(text, newline)
        if reason or new_text is None:
            continue
        # страховка: кроме вставленных строк ничего не изменилось — строки (m) и содержание на месте
        remaining = new_text.split(newline)
        for line in added:
            remaining.remove(line)
        if remaining != text.split(newline):
            raise NaError(f"Миграция остановлена: вставка в {change['path']} изменила бы содержание карточки", exit_code=1)
        path.write_bytes((codecs.BOM_UTF8 if bom else b"") + new_text.encode("utf-8"))
        written.append(change["path"])
    return written


MIGRATIONS: list[Migration] = [
    Migration("0001-record-schema-version", 1, "Зафиксировать версию схемы для папки, созданной до 0.6.0", _plan_record_version, _apply_record_version),
    Migration("0002-name-variant-fields", 2, "Добавить пустые поля speech_variants и asr_variants во frontmatter карточек людей и организаций (строки (m) и содержание не меняются)", _plan_name_variants, _apply_name_variants),
]


def build_plan(root: Path) -> dict[str, Any]:
    info = describe(root)
    if info["state"] == "invalid":
        raise NaError(info["message"])
    if info["state"] == "newer_than_plugin":
        raise NaError(info["message"])
    if info["state"] == "new":
        return {"state": "new", "from_version": None, "to_version": CURRENT_SCHEMA_VERSION, "steps": [],
                "creates": [WORKSPACE_FILE], "nothing_to_do": False, "workspace": info}
    if info["state"] == "current":
        recorded = info.get("plugin_versions") or []
        return {"state": "current", "from_version": info["schema_version"], "to_version": CURRENT_SCHEMA_VERSION, "steps": [], "creates": [],
                "nothing_to_do": plugin_version() in recorded, "workspace": info}
    from_version = info["schema_version"] or 0
    steps = []
    for migration in MIGRATIONS:
        if migration.to_version <= from_version:
            continue
        plan = migration.plan(root)
        steps.append({"id": migration.id, "to_version": migration.to_version, "title": migration.title, **plan})
    return {"state": "needs_migration", "from_version": info["schema_version"], "to_version": CURRENT_SCHEMA_VERSION, "steps": steps, "creates": [], "nothing_to_do": False, "workspace": info}


def run_migrate(root: Path, confirm: bool) -> dict[str, Any]:
    plan = build_plan(root)
    result: dict[str, Any] = {"valid": True, "confirmed": confirm, "plan": plan, "applied": [], "files_written": []}
    if plan["nothing_to_do"]:
        result["status"] = "nothing_to_do"
        return result
    if not confirm:
        result["status"] = "plan"
        return result
    if plan["state"] == "new":
        write_json(root / WORKSPACE_FILE, _new_document("new_workspace", CURRENT_SCHEMA_VERSION, "migrate"))
        result.update(status="applied", files_written=[WORKSPACE_FILE])
        return result
    if plan["state"] == "current":
        data = read_workspace(root)["data"]
        _note_plugin_version(root, data, "migrate")
        result.update(status="applied", files_written=[WORKSPACE_FILE])
        return result
    by_id = {migration.id: migration for migration in MIGRATIONS}
    written: list[str] = []
    for step in plan["steps"]:
        migration = by_id[step["id"]]
        fresh_plan = migration.plan(root)  # пересчёт непосредственно перед записью: план идемпотентен
        changed = migration.apply(root, fresh_plan)
        data = read_workspace(root)["data"]
        data["schema_version"] = migration.to_version
        data["migrations_applied"] = [
            *data.get("migrations_applied", []),
            {"id": migration.id, "to_version": migration.to_version, "applied_at": now_iso(), "plugin_version": plugin_version(), "engine_version": ENGINE_VERSION,
             "confirmation": "user_confirmation", "files_changed": changed, "skipped": fresh_plan.get("skipped", [])},
        ]
        known = {entry.get("plugin_version") for entry in data.get("plugin_versions", []) if isinstance(entry, dict)}
        if plugin_version() not in known:
            data["plugin_versions"] = [*data.get("plugin_versions", []), _version_entry("migrate")]
        write_json(root / WORKSPACE_FILE, data)
        written.extend(path for path in changed if path not in written)
        result["applied"].append({"id": migration.id, "to_version": migration.to_version, "files_changed": changed})
    if WORKSPACE_FILE not in written:
        written.append(WORKSPACE_FILE)
    result.update(status="applied", files_written=written)
    return result


def migrate_lines(result: dict[str, Any]) -> list[str]:
    plan = result["plan"]
    status = result["status"]
    if status == "nothing_to_do":
        return [f"Миграции не нужны: схема базы {plan['from_version']} актуальна, версия плагина уже записана. Ничего не изменено."]
    if plan["state"] == "new":
        if status == "plan":
            return [f"Папка пуста. С --confirm будет создан {WORKSPACE_FILE} со схемой {CURRENT_SCHEMA_VERSION}. Ничего не изменено."]
        return [f"Создан {WORKSPACE_FILE}: схема {CURRENT_SCHEMA_VERSION}."]
    if plan["state"] == "current":
        if status == "plan":
            return [f"Схема {plan['from_version']} актуальна. С --confirm в {WORKSPACE_FILE} будет записана текущая версия плагина. Ничего не изменено."]
        return [f"В {WORKSPACE_FILE} записана текущая версия плагина."]
    if status == "plan":
        source = plan["from_version"] if plan["from_version"] is not None else "не зафиксирована"
        lines = [f"План миграции: схема {source} → {plan['to_version']}. Ничего не изменено; применить: na.py migrate --confirm"]
        for step in plan["steps"]:
            lines.append(f"  [{step['id']}] {step['title']}: изменений {len(step['changes'])}, пропусков {len(step['skipped'])}")
            lines += [f"    {change['path']}: {change['details']}" for change in step["changes"]]
            lines += [f"    пропуск {item['path']}: {item['reason']}" for item in step["skipped"]]
        lines.append("Реестры и журнал событий миграции не трогают.")
        return lines
    lines = [f"Миграция применена по подтверждению: схема → {plan['to_version']}; файлов изменено {len(result['files_written'])}."]
    lines += [f"  [{item['id']}] файлов: {len(item['files_changed'])}" for item in result["applied"]]
    lines.append("В режиме проекта claude.ai запишите изменённые файлы в проект тем же путём.")
    return lines
