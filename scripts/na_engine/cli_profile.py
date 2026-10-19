"""Команда `na.py profile set --meeting <id> --profile light|full` — профиль разбора встречи."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .analysis_profile import PROFILE_FILES, PROFILE_LABELS


def register(sub: Any, root_parent: argparse.ArgumentParser) -> None:
    profile = sub.add_parser("profile", help="Профиль разбора встречи: set (full | light)", parents=[root_parent])
    profile_sub = profile.add_subparsers(dest="subcommand", required=True)
    set_cmd = profile_sub.add_parser("set", help="Записать профиль разбора в review.yaml", parents=[root_parent])
    set_cmd.add_argument("--meeting", required=True)
    set_cmd.add_argument("--profile", dest="analysis_profile", required=True, choices=list(PROFILE_FILES))


def handle(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], list[str], int] | None:
    if getattr(args, "command", None) != "profile":
        return None
    from .analysis_profile import set_profile

    result = set_profile(root, args.meeting, args.analysis_profile)
    lines = [
        f"Профиль разбора {result['meeting_id']}: {result['analysis_profile']} — {PROFILE_LABELS[result['analysis_profile']]} "
        f"(было: {result['previous_profile']}); записано в review.yaml, source=user_confirmation.",
        f"Обязательные артефакты: {', '.join(result['required'])}.",
    ]
    if result["skipped_by_profile"]:
        lines.append(f"Не выполняются по профилю: {', '.join(result['skipped_by_profile'])}.")
    if result["missing_required"]:
        lines.append(f"Ещё не подготовлены: {', '.join(result['missing_required'])}.")
    lines.append("Дальше: na.py refresh --meeting … (validate → report → followup → manifest).")
    return result, lines, 0
