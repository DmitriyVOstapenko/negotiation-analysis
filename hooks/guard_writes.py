#!/usr/bin/env python3
"""PreToolUse-хук плагина negotiation-analysis: защита базы знаний и исходных материалов встречи.

Срабатывает на запись файлов любым путём, который видит хост:

- инструменты правки файлов — Write, Edit, MultiEdit, NotebookEdit;
- команды оболочки — Bash, PowerShell (и инструменты оболочки других хостов): цели записи
  извлекаются из команды эвристикой — перенаправления (>, >>, &>), tee, sed -i / perl -i,
  cp / mv / install / rsync / ln, rm / unlink / shred, truncate, touch, dd of=, python -c и
  python с heredoc (open(…, 'w'), Path(…).write_text, shutil, os.remove …), node -e, командлеты
  PowerShell (Set-Content, Out-File, Remove-Item …). Вызов scripts/na.py — законный путь записи в
  базу, его собственная запись не проверяется (перенаправление его вывода в базу — проверяется);
- заплатка apply_patch (Codex): пути из заголовков «*** Add/Update/Delete File:» и «*** Move to:».

Если цель лежит в рабочей папке системы, хук:

- запрещает прямую запись, правку и удаление в knowledge/** — база меняется только через навык
  knowledge-update (проект изменений → diff → явное «да» пользователя → na.py knowledge apply
  --confirm);
- запрещает правку и удаление исходных материалов встречи: negotiations/<id>/transcript.txt и
  заметки провайдера (note.md, source_note.md, note_plaud.md), а также удаление или перенос
  каталога встречи и самой рабочей папки;
- для knowledge/strategies/** (стратегические карточки, которые навык conversation-strategy
  сохраняет напрямую по выбору пользователя) просит подтверждения пользователя.

Для команд оболочки дополнительно проверяются команды na.py, которые фиксируют решение
владельца (guard_decisions.py): они проходят только после ответа владельца в транскрипте.

Рабочая папка определяется по признакам: каталог с файлом .na-workspace, или с
knowledge/workspace.json, или с system/workspace.json, или с knowledge/events/ledger.jsonl, или
с подкаталогами knowledge/ и negotiations/ одновременно. Берётся ближайший такой предок цели.
Вне рабочей папки хук ничего не делает. В рабочей копии режима проекта claude.ai (корень с
именем na-workspace) разрешено только создавать новые файлы — так документ проекта загружается
в копию.

Любая ошибка разбора входа или файловой системы — пропуск (fail-open): хук не должен мешать
посторонней работе. Разбор команды — эвристика: запись через переменную, вычисляемый путь или
отдельный скрипт он не видит; это страховка, а не замена правил навыков. Ответ — JSON PreToolUse
(hookSpecificOutput.permissionDecision) с кодом 0; пустой вывод — «решения нет».

Только стандартная библиотека Python 3; сети нет.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _na_hooklib import looks_like_patch, patch_targets, shell_targets  # noqa: E402

FILE_TOOLS = {"Write": "write", "Edit": "modify", "MultiEdit": "modify", "NotebookEdit": "modify"}
PATH_KEYS = ("file_path", "notebook_path", "path")
SHELL_TOOL_RE = re.compile(r"(?i)bash|shell|powershell|exec")
SOURCE_FILES = {"transcript.txt", "note.md", "source_note.md", "note_plaud.md"}
PROJECT_COPY_DIR = "na-workspace"
RANK = {"knowledge": 3, "source": 3, "strategies": 1}


def _isfile(*parts: str) -> bool:
    try:
        return os.path.isfile(os.path.join(*parts))
    except (OSError, ValueError):
        return False


def _isdir(*parts: str) -> bool:
    try:
        return os.path.isdir(os.path.join(*parts))
    except (OSError, ValueError):
        return False


def is_workspace_root(directory: str) -> bool:
    return (
        _isfile(directory, ".na-workspace")
        or _isfile(directory, "knowledge", "workspace.json")
        or _isfile(directory, "system", "workspace.json")
        or _isfile(directory, "knowledge", "events", "ledger.jsonl")
        or (_isdir(directory, "knowledge") and _isdir(directory, "negotiations"))
    )


def _components(path: str) -> list[str]:
    drive, rest = os.path.splitdrive(path)
    pieces = [piece for piece in rest.replace("\\", "/").split("/") if piece]
    head = drive + os.sep if drive or rest.startswith(("/", "\\")) else ""
    return [head] + pieces if head else pieces


def classify(path: str, kind: str = "write") -> tuple[str, str, list[str]] | None:
    """(зона, корень рабочей папки, путь внутри корня) для защищённой цели, иначе None."""
    if kind in ("delete", "modify") and is_workspace_root(path):
        return "knowledge", path, ["."]
    components = _components(path)
    for depth in range(len(components) - 1, 0, -1):
        root = os.path.join(*components[:depth])
        if not is_workspace_root(root):
            continue
        inner = components[depth:]
        lowered = [piece.casefold() for piece in inner]
        if lowered[0] == "knowledge":
            return ("strategies" if len(inner) >= 2 and lowered[1] == "strategies" else "knowledge"), root, inner
        if lowered[0] == "negotiations":
            if len(inner) == 3 and lowered[2] in SOURCE_FILES:
                return "source", root, inner
            if len(inner) <= 2 and kind == "delete":
                return "source", root, inner
        return None  # ближайшая рабочая папка найдена, цель в ней не защищена
    return None


def _absolute(value: str, event: dict) -> str:
    path = os.path.expanduser(value.strip())
    if not os.path.isabs(path):
        base = event.get("cwd") if isinstance(event.get("cwd"), str) and event.get("cwd") else os.getcwd()
        path = os.path.join(base, path)
    return os.path.normpath(os.path.abspath(path))


def targets(event: dict) -> tuple[list[tuple[str, str]], str]:
    """Цели записи вызова: [(абсолютный путь, kind)], способ записи для сообщения."""
    tool = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return [], ""
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and event.get("cwd") else os.getcwd()
    if tool in FILE_TOOLS:
        for key in PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return [(_absolute(value, event), FILE_TOOLS[tool])], f"инструмент {tool}"
        return [], ""
    patch = next((tool_input.get(key) for key in ("command", "patch", "input") if isinstance(tool_input.get(key), str)), None)
    if tool == "apply_patch" or (patch and "file_path" not in tool_input and looks_like_patch(patch)):
        return (patch_targets(patch, cwd) if patch else []), "заплатка apply_patch"
    command = tool_input.get("command")
    if SHELL_TOOL_RE.search(tool) and isinstance(command, str):
        return shell_targets(command, cwd, powershell="powershell" in tool.lower()), "команда оболочки"
    return [], ""


def _expand_candidates(path: str) -> list[str]:
    candidates = [path]
    try:
        real = os.path.realpath(path)
        if real != path:
            candidates.append(real)
    except (OSError, ValueError):
        pass
    return candidates


def write_decision(event: dict) -> tuple[str, str] | None:
    """(deny|ask, причина) по целям записи, иначе None."""
    found_targets, via = targets(event)
    worst: tuple[str, str, list[str], str, str] | None = None
    for path, kind in found_targets:
        for candidate in _expand_candidates(path):
            result = classify(candidate, kind)
            if not result:
                continue
            zone, root, inner = result
            if os.path.basename(os.path.normpath(root)) == PROJECT_COPY_DIR and kind == "write" and not os.path.exists(candidate):
                continue  # загрузка документа проекта в пустую рабочую копию
            if worst is None or RANK[zone] > RANK[worst[0]]:
                worst = (zone, root, inner, kind, candidate)
    if worst is None:
        return None
    zone, _, inner, kind, _ = worst
    shown = "/".join(inner)
    action = {"write": "запись", "modify": "правка", "delete": "удаление или перенос"}.get(kind, "запись")
    if zone == "knowledge":
        return "deny", (
            f"negotiation-analysis: база знаний напрямую не меняется ({action}: {shown}; {via}). "
            "База меняется только через навык knowledge-update: проект изменений "
            "negotiations/<id>/knowledge_proposal.json → na.py knowledge diff → явное «да» пользователя → "
            "na.py knowledge apply --confirm. Скрипты na.py пишут в базу сами; поправьте проект изменений, а не файл базы."
        )
    if zone == "source":
        return "deny", (
            f"negotiation-analysis: исходный материал встречи неизменяем ({action}: {shown}; {via}). "
            "Уточнения и исправления распознавания — в review.yaml как подтверждения пользователя "
            "(source: user_confirmation); другая версия записи — новая регистрация через na.py source create; "
            "удалить запись может только сам владелец."
        )
    return "ask", (
        f"negotiation-analysis: запись стратегической карточки ({shown}; {via}) — только если пользователь сам выбрал "
        "сохранить разбор (навык conversation-strategy, раздел о сохранении). Подтвердите запись."
    )


def decide(event: dict) -> dict | None:
    if not isinstance(event, dict) or event.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    verdicts: list[tuple[str, str]] = []
    try:
        verdict = write_decision(event)
        if verdict:
            verdicts.append(verdict)
    except Exception:  # fail-open по этой части
        pass
    try:
        import guard_decisions

        owner = guard_decisions.decide(event)
        if owner:
            verdicts.append(("deny", owner["hookSpecificOutput"]["permissionDecisionReason"]))
    except Exception:  # fail-open по этой части
        pass
    if not verdicts:
        return None
    kind = "deny" if any(item[0] == "deny" for item in verdicts) else "ask"
    reason = " ".join(text for decision, text in verdicts if decision == kind)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind, "permissionDecisionReason": reason}}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        decision = decide(json.loads(raw))
    except Exception:  # fail-open: любая ошибка — без решения
        return 0
    if decision:
        sys.stdout.write(json.dumps(decision, ensure_ascii=True))  # ASCII: не зависит от кодировки консоли Windows
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
