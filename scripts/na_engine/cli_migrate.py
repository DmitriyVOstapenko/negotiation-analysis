"""Команда `na.py migrate [--confirm]`: план и применение миграций схемы рабочей папки."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def register(sub: Any, root_parent: argparse.ArgumentParser) -> None:
    migrate = sub.add_parser(
        "migrate",
        help="Миграции схемы рабочей папки: без --confirm — план, с --confirm — применить",
        parents=[root_parent],
    )
    migrate.add_argument("--confirm", action="store_true", help="Применить план (только после того, как пользователь его увидел)")


def handle(args: argparse.Namespace, root: Path) -> tuple[dict, list[str], int] | None:
    if getattr(args, "command", None) != "migrate":
        return None
    from .workspace_schema import migrate_lines, run_migrate

    result = run_migrate(root, bool(args.confirm))
    return result, migrate_lines(result), 0
