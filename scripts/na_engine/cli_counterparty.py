"""Команда `na.py counterparty --org <id|название> [--since YYYY-MM-DD] [--until YYYY-MM-DD]`."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def register(sub: Any, root_parent: argparse.ArgumentParser) -> None:
    counterparty = sub.add_parser(
        "counterparty",
        help="Ревью контрагента за период → system/counterparty/<org>.{md,json}",
        parents=[root_parent],
    )
    counterparty.add_argument("--org", required=True, help="id организации или название (как в карточке или разборе встречи)")
    counterparty.add_argument("--since", help="Начало периода YYYY-MM-DD (включительно)")
    counterparty.add_argument("--until", help="Конец периода YYYY-MM-DD (включительно)")


def handle(args: argparse.Namespace, root: Path) -> tuple[dict, list[str], int] | None:
    if getattr(args, "command", None) != "counterparty":
        return None
    from .counterparty import run_counterparty, summary_lines

    result = run_counterparty(root, args.org, args.since, args.until)
    return result, summary_lines(result), 0
