#!/usr/bin/env python3
"""Патч плагина «Иван» (coman-ivan-cowork): в рабочей папке переговоров Иван отступает.

Зачем. Иван (github.com/Coman-os/ivan, MIT) включается на весь аккаунт, а на уровне папки
или проекта его не выключить. В рабочей папке negotiation-analysis он мешает (docs/IVAN.md):
требует «решать самому» там, где решение за владельцем; блокирует запись отчёта без карточки
оформления; отдаёт хвост ответа внешнему судье; грузит ~85 КБ правил в каждую сессию. Хуки
Ивана получают рабочую папку сессии, поэтому выключатель по папке возможен внутри самого Ивана.

Признак рабочей папки — файл `knowledge/workspace.json` с
`"workspace_format": "negotiation-analysis-workspace"` (его пишет только движок na.py).

Что меняется (пять файлов, один новый; вне папки переговоров Иван работает как прежде):
  hooks/_na_workspace.py              — новый модуль: поиск рабочей папки от cwd или пути файла;
  hooks/check_closing_handoff.py      — в папке переговоров молчит: ни предупреждения о «передаче
                                        хода», ни вызова внешнего судьи;
  hooks/post_write_md_check.py        — .md внутри папки переговоров карточки не требует;
  hooks/session_start_load_rules.py   — вместо 85 КБ правил впрыскивает короткую оговорку;
  skills/close-session/SKILL.md       — журнал сессии в папке переговоров не пишется;
  skills/ivan-rules/SKILL.md          — оговорка на случай, если правила всё же в контексте.

Паспорт сборки Ивана (BUILD-MANIFEST.json) не трогается: его самопроверка честно сообщит
«файлы отличаются от паспорта» — так и задумано. После обновления Ивана патч накладывается заново.

Использование:
  python3 tools/ivan_patch.py --plugin-dir <копия плагина> [--verify] [--package <файл.plugin>]
  python3 tools/ivan_patch.py --plugin-dir <копия> --check      # только проверить, наложен ли патч

Код выхода: 0 — готово (или уже наложен); 1 — якорь не найден (другая версия Ивана) или
проверка не прошла; 2 — ошибка использования.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MARK = "negotiation-analysis patch"
SUPPORTED_VERSIONS = ("2.7.3",)

WORKSPACE_MODULE = '''"""Рабочая папка плагина negotiation-analysis — признак, по которому Иван отступает.

[negotiation-analysis patch] Добавлено патчем tools/ivan_patch.py из репозитория
DmitriyVOstapenko/negotiation-analysis. Вне рабочей папки переговоров ничего не меняет.

Признак: файл knowledge/workspace.json с "workspace_format": "negotiation-analysis-workspace"
(его пишет только движок na.py). Ищется вверх от стартовой точки (до 8 уровней) и на один
уровень вниз (подключена папка, внутри которой лежит рабочая папка).
"""

import json
import os
import sys

MARKER = os.path.join("knowledge", "workspace.json")
WORKSPACE_FORMAT = "negotiation-analysis-workspace"
MAX_UP = 8
MAX_CHILDREN = 200


def _is_workspace(path):
    marker = os.path.join(path, MARKER)
    if not os.path.isfile(marker):
        return False
    try:
        with open(marker, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("workspace_format") == WORKSPACE_FORMAT


def workspace_root(start):
    """Корень рабочей папки переговоров для точки start (каталог или файл), иначе None."""
    if not start:
        return None
    path = os.path.abspath(start)
    if os.path.isfile(path) or not os.path.isdir(path):
        path = os.path.dirname(path)
    probe = path
    for _ in range(MAX_UP):
        if _is_workspace(probe):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        with os.scandir(path) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_CHILDREN:
                    break
                try:
                    if entry.is_dir(follow_symlinks=False) and _is_workspace(entry.path):
                        return entry.path
                except OSError:
                    continue
    except OSError:
        pass
    return None


def in_workspace(hook_input=None, path=None):
    """True, если событие относится к рабочей папке переговоров.

    path — путь файла (для проверок записи); hook_input — payload хука с полем cwd.
    """
    if path and workspace_root(path):
        return True
    cwd = None
    if isinstance(hook_input, dict):
        cwd = hook_input.get("cwd")
    if not cwd:
        cwd = os.getcwd()
    return workspace_root(cwd) is not None


def from_stdin():
    """Корень рабочей папки по payload из stdin (для хуков, которые stdin сами не читают)."""
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    cwd = None
    if raw.strip():
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                cwd = payload.get("cwd")
        except ValueError:
            cwd = None
    return workspace_root(cwd or os.getcwd())
'''

SESSION_NOTE = (
    "# Папка переговоров — правила Ивана здесь не загружены\n\n"
    "Это рабочая папка плагина negotiation-analysis ({root}). По решению владельца в ней действуют "
    "правила этого плагина, а не Ивана: решения владельца — согласие на обработку записи, карта "
    "спикеров, сомнительные пункты учёта, применение изменений базы, отправка — задаются вопросом и "
    "самостоятельно не принимаются; проверки оформления .md и завершающего хода здесь отключены; "
    "навыки Ивана (close-session, kh-check, ivan-setup) к этой папке не применять, журнал сессии и "
    "содержание встреч в папку Ивана не переносить. Вне этой папки Иван работает как обычно."
)

EDITS = [
    {
        "file": "hooks/check_closing_handoff.py",
        "anchor": "    transcript = load_messages(hook_input)\n",
        "insert_before": True,
        "text": (
            "    # [negotiation-analysis patch] В рабочей папке переговоров решения владельца задаются\n"
            "    # вопросом (шлюз, карта спикеров, применение базы, отправка): проверка «передачи хода»\n"
            "    # здесь молчит, и хвост ответа внешнему судье не уходит.\n"
            "    import _na_workspace\n"
            "    if _na_workspace.in_workspace(hook_input):\n"
            "        return\n\n"
        ),
    },
    {
        "file": "hooks/post_write_md_check.py",
        "anchor": "    basename = os.path.basename(file_path)\n    if basename in (SKIP_FILENAMES | _env_set(\"IVAN_MD_SKIP_FILES\")):\n",
        "insert_before": True,
        "text": (
            "    # [negotiation-analysis patch] Документы рабочей папки переговоров (отчёты, заметки\n"
            "    # источника, карточки базы) карточки оформления не требуют; исходники встречи неизменяемы.\n"
            "    import _na_workspace\n"
            "    if _na_workspace.in_workspace(hook_input, path=file_path):\n"
            "        return\n\n"
        ),
    },
    {
        "file": "hooks/session_start_load_rules.py",
        "anchor": "def main() -> None:\n",
        "insert_before": False,
        "text": (
            "    # [negotiation-analysis patch] В рабочей папке переговоров правила Ивана не грузятся:\n"
            "    # вместо них — короткая оговорка, остальное решают правила плагина negotiation-analysis.\n"
            "    import _na_workspace\n"
            "    _na_root = _na_workspace.from_stdin()\n"
            "    if _na_root:\n"
            "        print(json.dumps({\"hookSpecificOutput\": {\n"
            "            \"hookEventName\": \"SessionStart\",\n"
            "            \"additionalContext\": " + json.dumps(SESSION_NOTE, ensure_ascii=False) + ".replace(\"{root}\", _na_root),\n"
            "        }}, ensure_ascii=False))\n"
            "        return\n"
        ),
    },
    {
        "file": "skills/close-session/SKILL.md",
        "anchor": "\n---\n",  # конец frontmatter (первое вхождение после первой строки)
        "insert_before": False,
        "frontmatter": True,
        "text": (
            "\n> **Исключение [negotiation-analysis patch].** В рабочей папке плагина negotiation-analysis\n"
            "> (есть `knowledge/workspace.json` с `workspace_format: negotiation-analysis-workspace`) этот\n"
            "> навык не выполнять: журнал сессии туда не пишется и в папку Ивана не копируется — содержание\n"
            "> встреч остаётся в папке переговоров. Сессия переговоров закрывается шагом 10 навыка\n"
            "> `meeting-analysis` (проект изменений базы), о чём и сказать владельцу.\n"
        ),
    },
    {
        "file": "skills/ivan-rules/SKILL.md",
        "anchor": "\n---\n",
        "insert_before": False,
        "frontmatter": True,
        "text": (
            "\n> **Исключение [negotiation-analysis patch].** В рабочей папке плагина negotiation-analysis\n"
            "> (есть `knowledge/workspace.json` с `workspace_format: negotiation-analysis-workspace`) эти\n"
            "> правила хуком не грузятся. Если они всё же в контексте: §28 к решениям владельца плагина —\n"
            "> согласие на обработку, карта спикеров, сомнительные пункты, применение базы, отправка — не\n"
            "> применять, они задаются вопросом; карточки оформления к отчётам и исходникам встречи не\n"
            "> требовать; навыки Ивана к этой папке не применять.\n"
        ),
    },
]


def plugin_version(plugin_dir: Path) -> str | None:
    try:
        with open(plugin_dir / ".claude-plugin" / "plugin.json", encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, ValueError):
        return None


def apply_edit(plugin_dir: Path, edit: dict, dry: bool) -> str:
    path = plugin_dir / edit["file"]
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        return "already"
    anchor = edit["anchor"]
    if edit.get("frontmatter"):
        # frontmatter: первая строка '---', затем первое '\n---\n' после неё
        if not text.startswith("---\n"):
            raise LookupError(f"{edit['file']}: нет YAML-frontmatter")
        index = text.find(anchor, 4)
        if index < 0:
            raise LookupError(f"{edit['file']}: не найден конец frontmatter")
        index += len(anchor)
        new = text[:index] + edit["text"] + text[index:]
    else:
        if text.count(anchor) != 1:
            raise LookupError(f"{edit['file']}: якорь найден {text.count(anchor)} раз, ожидался 1 — другая версия Ивана?")
        index = text.find(anchor)
        if edit["insert_before"]:
            new = text[:index] + edit["text"] + text[index:]
        else:
            index += len(anchor)
            new = text[:index] + edit["text"] + text[index:]
    if not dry:
        path.write_text(new, encoding="utf-8")
    return "patched"


def apply(plugin_dir: Path, dry: bool = False) -> list[tuple[str, str]]:
    results = []
    module = plugin_dir / "hooks" / "_na_workspace.py"
    if module.exists() and MARK in module.read_text(encoding="utf-8"):
        results.append(("hooks/_na_workspace.py", "already"))
    else:
        if not dry:
            module.write_text(WORKSPACE_MODULE, encoding="utf-8")
        results.append(("hooks/_na_workspace.py", "written"))
    for edit in EDITS:
        results.append((edit["file"], apply_edit(plugin_dir, edit, dry)))
    return results


def check(plugin_dir: Path) -> bool:
    module = plugin_dir / "hooks" / "_na_workspace.py"
    if not module.exists():
        return False
    for edit in EDITS:
        if MARK not in (plugin_dir / edit["file"]).read_text(encoding="utf-8"):
            return False
    return True


# ---------------------------------------------------------------------------
# Проверка живыми запусками хуков
# ---------------------------------------------------------------------------

def _run_hook(plugin_dir: Path, hook: str, payload: dict, env_extra: dict | None = None) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_dir)
    env["IVAN_HANDOFF_JUDGE"] = "/nonexistent/no-judge"  # внешний судья не вызывается
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(plugin_dir / "hooks" / hook)],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
        env=env, cwd=str(plugin_dir / "hooks"), timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _unescape(text: str) -> str:
    """Вывод хуков Ивана — JSON с \\uXXXX; для проверки и показа вернуть кириллицу."""
    try:
        return json.loads(json.dumps(text).replace("\\\\u", "\\u"))
    except ValueError:
        return text


def verify(plugin_dir: Path) -> list[tuple[str, bool, str]]:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "Переговоры"
        (ws / "knowledge").mkdir(parents=True)
        (ws / "negotiations" / "2026-11-03-test").mkdir(parents=True)
        (ws / "knowledge" / "workspace.json").write_text(json.dumps({
            "workspace_format": "negotiation-analysis-workspace", "schema_version": 2}), encoding="utf-8")
        report = ws / "negotiations" / "2026-11-03-test" / "report.md"
        report.write_text("# Отчёт по встрече\n\n**Данные:** транскрипт.\n", encoding="utf-8")
        other = Path(tmp) / "Другой проект"
        other.mkdir()
        other_doc = other / "note.md"
        other_doc.write_text("# Заметка без карточки\n\nтекст\n", encoding="utf-8")

        # 1. Правила на старте сессии
        code, out, err = _run_hook(plugin_dir, "session_start_load_rules.py", {"hook_event_name": "SessionStart", "cwd": str(ws)})
        ok = "Папка переговоров" in out and "Правила Ивана — активны" not in out
        results.append(("старт сессии в папке переговоров: короткая оговорка вместо правил", ok, out[:160].replace("\n", " ")))
        code, out, err = _run_hook(plugin_dir, "session_start_load_rules.py", {"hook_event_name": "SessionStart", "cwd": str(other)})
        ok = "Правила Ивана" in out and "Папка переговоров" not in out
        results.append(("старт сессии вне папки: правила Ивана как прежде", ok, out[:120].replace("\n", " ")))

        # 2. Карточка .md
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(ws), "tool_input": {"file_path": str(report)}}
        code, out, err = _run_hook(plugin_dir, "post_write_md_check.py", payload)
        ok = code == 0 and "block" not in out + err
        results.append(("report.md без карточки в папке переговоров: без замечаний", ok, (out + err)[:160].replace("\n", " ")))
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(other), "tool_input": {"file_path": str(other_doc)}}
        code, out, err = _run_hook(plugin_dir, "post_write_md_check.py", payload)
        ok = "block" in out + err or code != 0
        results.append(("note.md без карточки вне папки: замечание Ивана как прежде", ok, (out + err)[:160].replace("\n", " ")))

        # 3. Передача хода (Stop)
        transcript = Path(tmp) / "transcript.jsonl"
        # Уникальный хвост: Иван помнит уже показанные хвосты и повторно отвечает мягче.
        salt = os.urandom(4).hex()
        transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": (
            f"Разбор встречи {salt} готов. Что требует вашего решения: подтвердить карту спикеров и утвердить "
            "применение изменений базы. Варианты: подтвердить / поправить / не знаю — скажите.")}]}}, ensure_ascii=False) + "\n", encoding="utf-8")
        payload = {"hook_event_name": "Stop", "cwd": str(ws), "transcript_path": str(transcript), "stop_hook_active": False}
        code, out, err = _run_hook(plugin_dir, "check_closing_handoff.py", payload)
        ok = code == 0 and not (out + err).strip()
        results.append(("ответ с вопросом владельцу в папке переговоров: Stop-хук молчит", ok, (out + err)[:160].replace("\n", " ")))
        payload = {"hook_event_name": "Stop", "cwd": str(other), "transcript_path": str(transcript), "stop_hook_active": False}
        code, out, err = _run_hook(plugin_dir, "check_closing_handoff.py", payload)
        ok = "передач" in _unescape(out + err).lower()
        results.append(("тот же ответ вне папки: предупреждение Ивана как прежде", ok, _unescape(out + err)[:160].replace("\n", " ")))

        # 4. Запись .md командой оболочки (посредник → проверка карточки)
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(ws),
                   "tool_input": {"command": f"cat > '{report}' <<'EOF'\n# Отчёт\nEOF"}}
        code, out, err = _run_hook(plugin_dir, "post_bash_write_relay.py", payload)
        ok = "карточк" not in (out + err).lower()
        results.append(("heredoc в report.md в папке переговоров: посредник без замечаний", ok, (out + err)[:160].replace("\n", " ")))
    return results


def package(plugin_dir: Path, out: Path) -> int:
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(plugin_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for name in files:
                if name in (".in_use", ".DS_Store") or name.endswith(".pyc"):
                    continue
                full = Path(root) / name
                zf.write(full, full.relative_to(plugin_dir).as_posix())
                count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Патч Ивана: в рабочей папке переговоров Иван отступает.")
    parser.add_argument("--plugin-dir", required=True, help="Каталог плагина coman-ivan-cowork (копия установленного или cowork/ из репозитория)")
    parser.add_argument("--check", action="store_true", help="Только проверить, наложен ли патч")
    parser.add_argument("--dry-run", action="store_true", help="Показать, что будет изменено, не меняя файлов")
    parser.add_argument("--verify", action="store_true", help="После наложения прогнать хуки на синтетических событиях")
    parser.add_argument("--package", metavar="FILE", help="Собрать .plugin из каталога после наложения")
    args = parser.parse_args(argv)

    plugin_dir = Path(args.plugin_dir).resolve()
    if not (plugin_dir / ".claude-plugin" / "plugin.json").exists():
        print(f"Не плагин: нет {plugin_dir / '.claude-plugin' / 'plugin.json'}", file=sys.stderr)
        return 2
    version = plugin_version(plugin_dir)
    if args.check:
        state = check(plugin_dir)
        print(f"Иван {version}: патч {'наложен' if state else 'НЕ наложен'}.")
        return 0 if state else 1
    if version not in SUPPORTED_VERSIONS:
        print(f"Иван {version}: патч проверен на {', '.join(SUPPORTED_VERSIONS)}; попробую по якорям.", file=sys.stderr)
    try:
        results = apply(plugin_dir, dry=args.dry_run)
    except LookupError as exc:
        print(f"Патч не наложен: {exc}", file=sys.stderr)
        return 1
    for name, state in results:
        print(f"  {state:8} {name}")
    if args.dry_run:
        return 0
    if args.verify:
        failed = 0
        for title, ok, detail in verify(plugin_dir):
            print(f"  [{'ok' if ok else 'FAIL'}] {title}" + ("" if ok else f" — {detail}"))
            failed += 0 if ok else 1
        if failed:
            print(f"Проверка не прошла: {failed}", file=sys.stderr)
            return 1
    if args.package:
        out = Path(args.package).resolve()
        count = package(plugin_dir, out)
        print(f"Пакет: {out} ({count} файлов, Иван {version} + патч)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
