"""Самопроверка установки и рабочей папки: na.py doctor. Только чтение, без сети.

Идеи самопроверки по ступеням, паспорта сборки с отпечатками файлов и пробы интерпретатора
взяты из плагина ivan (github.com/Coman-os/ivan, MIT, © 2026 Maxim Opilkin): session_start_selfcheck.py,
BUILD-MANIFEST.json, detect-python.py. Код написан заново.

Ступени для хуков и агентов: файл на месте · описан корректно · ссылки на скрипты целы.
Сработал ли хук на живом событии — отсюда не видно; doctor об этом говорит прямо.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .common import PLUGIN_ROOT, read_json_or_none

MANIFEST_FILE = "BUILD-MANIFEST.json"
MIN_PYTHON = (3, 10)
NEIGHBOR_MARKERS = ("coman-ivan",)


# ---------------------------------------------------------------------------
# Паспорт сборки
# ---------------------------------------------------------------------------


def _body_after_frontmatter(data: bytes) -> bytes:
    """Тело SKILL.md без YAML-шапки: при синхронизации claude.ai шапку переписывают."""
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4 :]
    return text.lstrip("\n").encode("utf-8")


def file_fingerprint(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    result = {"sha256": hashlib.sha256(data).hexdigest()}
    if path.name == "SKILL.md":
        result["body_sha256"] = hashlib.sha256(_body_after_frontmatter(data)).hexdigest()
    return result


def check_manifest(plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any]:
    manifest = read_json_or_none(plugin_root / MANIFEST_FILE)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return {"state": "absent", "message": "Паспорта сборки нет — плагин запущен из репозитория, а не из собранного пакета; сверка файлов пропущена."}
    changed: list[str] = []
    missing: list[str] = []
    for rel, expected in sorted(manifest["files"].items()):
        path = plugin_root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        actual = file_fingerprint(path)
        if path.name == "SKILL.md" and expected.get("body_sha256"):
            if actual.get("body_sha256") != expected["body_sha256"]:
                changed.append(rel)
        elif actual["sha256"] != expected.get("sha256"):
            changed.append(rel)
    state = "ok" if not changed and not missing else "changed"
    message = (
        f"Файлы совпадают с паспортом сборки {manifest.get('version')} ({len(manifest['files'])} файлов)."
        if state == "ok"
        else f"Отличия от паспорта сборки {manifest.get('version')}: изменено {len(changed)}, нет {len(missing)}. Переустановите плагин или сообщите, что правили файлы."
    )
    return {"state": state, "version": manifest.get("version"), "built_at": manifest.get("built_at"), "changed": changed, "missing": missing, "message": message}


# ---------------------------------------------------------------------------
# Интерпретатор
# ---------------------------------------------------------------------------


def check_python() -> dict[str, Any]:
    version = sys.version_info
    ok = (version.major, version.minor) >= MIN_PYTHON
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    notes: list[str] = []
    if not ok:
        notes.append(f"нужен Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} или новее")
    if encoding and encoding.replace("-", "") not in ("utf8",):
        notes.append(f"кодировка вывода {encoding}: кириллица может печататься с ошибками — задайте PYTHONUTF8=1")
    windows_hint = None
    if os.name == "nt":
        windows_hint = "На Windows команда python3 может отсутствовать или быть заглушкой магазина: пробуйте `py -3`, затем `python`, и проверяйте запуском `-c \"print(1)\"`."
    return {
        "state": "ok" if ok else "error",
        "version": platform.python_version(),
        "executable": sys.executable,
        "stdout_encoding": encoding or None,
        "platform": platform.platform(),
        "notes": notes,
        "windows_hint": windows_hint,
    }


def probe_interpreters() -> list[dict[str, Any]]:
    """Проба делом: какие команды реально запускают Python ≥ 3.10. Без сети, по 5 секунд на команду."""
    candidates = [["py", "-3"], ["python3"], ["python"]] if os.name == "nt" else [["python3"], ["python"]]
    results = []
    for command in candidates:
        try:
            completed = subprocess.run(
                [*command, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({"command": " ".join(command), "works": False, "detail": type(exc).__name__})
            continue
        out = completed.stdout.strip()
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", out)
        works = completed.returncode == 0 and bool(match) and (int(match.group(1)), int(match.group(2))) >= MIN_PYTHON
        results.append({"command": " ".join(command), "works": works, "detail": out or completed.stderr.strip()[:120]})
    return results


# ---------------------------------------------------------------------------
# Хуки и агенты
# ---------------------------------------------------------------------------


def _frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    result: dict[str, str] = {}
    for line in text[3:end].splitlines():
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


def check_hooks(plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any]:
    path = plugin_root / "hooks" / "hooks.json"
    if not path.is_file():
        return {"state": "absent", "message": "Хуков в пакете нет.", "problems": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"state": "error", "message": f"hooks/hooks.json не читается: {exc}", "problems": [str(exc)]}
    problems: list[str] = []
    registered: list[str] = []
    for event, groups in (data.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                parts = [hook.get("command") or ""] + [str(arg) for arg in hook.get("args") or []]
                for part in parts:
                    for script in re.findall(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/([^\s\"']+)", part):
                        if not (plugin_root / script).is_file():
                            problems.append(f"{event}: нет файла {script}")
                        else:
                            registered.append(f"{event} → {script}")
    state = "ok" if not problems else "error"
    message = (
        f"Хуки описаны: {', '.join(registered) or 'нет'}. Сработали ли они на живом событии — отсюда не видно: проверка — попытка прямой записи в knowledge/ должна быть остановлена."
        if state == "ok"
        else "Хуки описаны с ошибками: " + "; ".join(problems)
    )
    return {"state": state, "registered": registered, "problems": problems, "message": message}


def check_agents(plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any]:
    directory = plugin_root / "agents"
    if not directory.is_dir():
        return {"state": "absent", "agents": [], "problems": [], "message": "Субагентов в пакете нет."}
    agents: list[str] = []
    problems: list[str] = []
    for path in sorted(directory.glob("*.md")):
        meta = _frontmatter(path.read_text(encoding="utf-8"))
        if not meta or not meta.get("name") or not meta.get("description"):
            problems.append(f"{path.name}: нет name или description во frontmatter")
        else:
            agents.append(meta["name"])
    state = "ok" if not problems else "error"
    return {"state": state, "agents": agents, "problems": problems, "message": f"Субагенты: {', '.join(agents) or 'нет'}." if state == "ok" else "; ".join(problems)}


# ---------------------------------------------------------------------------
# Соседи и рабочая папка
# ---------------------------------------------------------------------------


def find_neighbors(plugin_root: Path = PLUGIN_ROOT, depth_up: int = 4) -> list[str]:
    """Есть ли рядом в каталоге плагинов Иван (coman-ivan-*). Только имена каталогов, без чтения содержимого."""
    found: set[str] = set()
    current = plugin_root.resolve()
    for _ in range(depth_up):
        current = current.parent
        if current == current.parent:
            break
        try:
            for first in current.iterdir():
                names = [first]
                if first.is_dir():
                    try:
                        names += list(first.iterdir())
                    except OSError:
                        pass
                for candidate in names:
                    if any(marker in candidate.name for marker in NEIGHBOR_MARKERS):
                        found.add(candidate.name)
        except OSError:
            continue
    return sorted(found)


def _inside_git(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def check_workspace(root: Path) -> dict[str, Any]:
    if not root.is_dir() or not ((root / "knowledge").is_dir() or (root / "negotiations").is_dir()):
        return {"state": "absent", "message": f"Рабочая папка системы не найдена в {root} (нет knowledge/ и negotiations/)."}
    from . import delivery, workspace_schema

    schema = workspace_schema.describe(root)
    notes: list[str] = [schema["message"]]
    state = "ok" if schema["state"] in ("current", "new") else "warning"
    secrets_dir = root / "delivery" / "secrets"
    token_file = secrets_dir / "telegram_bot_token"
    if token_file.is_file() and token_file.read_text(encoding="utf-8").strip():
        repo = _inside_git(root)
        ignore = root / "delivery" / ".gitignore"
        if repo is not None and not (ignore.is_file() and "secrets" in ignore.read_text(encoding="utf-8")):
            notes.append(f"Ключ бота лежит в папке под git ({repo}) без исключения: выполните deliver status — он создаст delivery/.gitignore.")
            state = "warning"
        if os.name == "posix" and token_file.stat().st_mode & 0o077:
            notes.append("Файл ключа бота доступен не только владельцу: chmod 600 delivery/secrets/telegram_bot_token.")
            state = "warning"
    config = delivery.load_config(root)
    channels = []
    if config.get("email_self"):
        channels.append("почта")
    if config.get("telegram"):
        channels.append("Telegram")
    notes.append(f"Отправка себе: {', '.join(channels) if channels else 'не настроена'}.")
    return {"state": state, "schema": schema, "notes": notes, "message": " ".join(notes)}


# ---------------------------------------------------------------------------
# Сводка
# ---------------------------------------------------------------------------


def run_doctor(root: Path, probe: bool = False) -> dict[str, Any]:
    plugin_version = None
    plugin_json = read_json_or_none(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
    if isinstance(plugin_json, dict):
        plugin_version = plugin_json.get("version")
    checks = {
        "python": check_python(),
        "manifest": check_manifest(),
        "hooks": check_hooks(),
        "agents": check_agents(),
        "workspace": check_workspace(root),
    }
    neighbors = find_neighbors()
    result: dict[str, Any] = {
        "plugin_version": plugin_version,
        "plugin_root": str(PLUGIN_ROOT),
        "checks": checks,
        "neighbors": neighbors,
        "errors": [name for name, check in checks.items() if check.get("state") == "error"],
        "warnings": [name for name, check in checks.items() if check.get("state") in ("warning", "changed")],
    }
    if probe:
        result["interpreters"] = probe_interpreters()
        working = {item["command"] for item in result["interpreters"] if item["works"]}
        if checks["hooks"].get("registered") and "python3" not in working:
            checks["hooks"]["state"] = "error"
            checks["hooks"]["message"] = (
                "Хуки вызываются командой python3, а она не запускает Python 3.10+ — защита базы и проверка решений "
                "владельца не работают. Установите Python так, чтобы работала команда python3 (на Windows — "
                "установщик python.org с добавлением в PATH или псевдоним python3)."
            )
            if "hooks" not in result["errors"]:
                result["errors"].append("hooks")
    return result


def doctor_lines(result: dict[str, Any]) -> list[str]:
    checks = result["checks"]
    python = checks["python"]
    lines = [f"negotiation-analysis {result['plugin_version'] or '?'}; Python {python['version']} ({python['executable']})."]
    for note in python["notes"]:
        lines.append(f"  Python: {note}.")
    if python.get("windows_hint"):
        lines.append(f"  {python['windows_hint']}")
    for name in ("manifest", "hooks", "agents", "workspace"):
        lines.append(f"  {checks[name]['message']}")
        if name == "manifest":
            for label, files in (("изменён", checks[name].get("changed") or []), ("нет файла", checks[name].get("missing") or [])):
                for rel in files[:8]:
                    lines.append(f"    — {label}: {rel}")
                if len(files) > 8:
                    lines.append(f"    — … и ещё {len(files) - 8}")
    if result.get("interpreters") is not None:
        working = [item["command"] for item in result["interpreters"] if item["works"]]
        lines.append(f"  Работающие команды Python: {', '.join(working) if working else 'не найдены'}.")
    if result["neighbors"]:
        lines.append(
            "  Рядом установлен плагин Ивана ("
            + ", ".join(result["neighbors"])
            + "); включён ли он, по файлам не видно. Для работы с переговорами его выключают: Customize → Plugins (docs/IVAN.md)."
        )
    if result["errors"]:
        lines.append(f"Итог: есть ошибки ({', '.join(result['errors'])}).")
    elif result["warnings"]:
        lines.append(f"Итог: работает, есть предупреждения ({', '.join(result['warnings'])}).")
    else:
        lines.append("Итог: всё на месте.")
    return lines
