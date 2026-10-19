"""Команды для работы с агентами плагина: длинные встречи по частям и отчёт верификации.

na.py parts plan  --meeting <id> [--max-chars N] [--context N]
na.py parts check --meeting <id> [--file <часть>]
na.py parts merge --meeting <id> [--artifact 07,08] [--force]
na.py verification check --meeting <id>
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def register(sub: Any, root_parent: argparse.ArgumentParser) -> None:
    parts = sub.add_parser("parts", help="Длинная встреча по частям: plan, check, merge", parents=[root_parent])
    parts_sub = parts.add_subparsers(dest="subcommand", required=True)
    plan = parts_sub.add_parser("plan", help="Разбить транскрипт на части по границам реплик (содержимое не печатается)", parents=[root_parent])
    plan.add_argument("--meeting", required=True)
    plan.add_argument("--max-chars", type=int, default=None, help="Предел объёма части в знаках (по умолчанию 25000)")
    plan.add_argument("--context", type=int, default=None, help="Сколько реплик до начала части читать для контекста (по умолчанию 3)")
    check = parts_sub.add_parser("check", help="Проверить файлы частей: контракт, цитаты, границы части", parents=[root_parent])
    check.add_argument("--meeting", required=True)
    check.add_argument("--file", dest="part_file", default=None, help="Один файл части (имя в analysis/parts/ или путь)")
    merge = parts_sub.add_parser("merge", help="Свести части в итоговые analysis/<NN>_*.json", parents=[root_parent])
    merge.add_argument("--meeting", required=True)
    merge.add_argument("--artifact", default=None, help="Какие артефакты сводить: 07 или 01,07 (по умолчанию все, у которых есть части)")
    merge.add_argument("--force", action="store_true", help="Перезаписать уже существующий итоговый файл")

    verification = sub.add_parser("verification", help="Отчёт независимой проверки: check", parents=[root_parent])
    verification_sub = verification.add_subparsers(dest="subcommand", required=True)
    vcheck = verification_sub.add_parser("check", help="Проверить analysis/verification_report.json: вердикты, адреса полей, цитаты", parents=[root_parent])
    vcheck.add_argument("--meeting", required=True)


def handle(args: argparse.Namespace, root: Path) -> tuple[dict, list[str], int] | None:
    command = getattr(args, "command", None)
    if command == "parts":
        from . import long_meeting

        if args.subcommand == "plan":
            result = long_meeting.run_plan(root, args.meeting, args.max_chars, args.context)
            return result, long_meeting.plan_lines(result), 0
        if args.subcommand == "check":
            result = long_meeting.run_check(root, args.meeting, args.part_file)
            return result, long_meeting.check_lines(result), 0 if result["valid"] else 1
        if args.subcommand == "merge":
            result = long_meeting.run_merge(root, args.meeting, args.artifact, args.force)
            return result, long_meeting.merge_lines(result), 0 if result["valid"] else 1
        return None
    if command == "verification":
        from . import verification

        if args.subcommand == "check":
            result = verification.run_check(root, args.meeting)
            return result, verification.check_lines(result), 0 if result["valid"] else 1
        return None
    return None
