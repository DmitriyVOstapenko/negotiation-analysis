"""Команда оценки разбора: na.py eval --meeting <id> --core <core.json>.

Код выхода 0 — PASS (все пункты ядра FULL, лишних искажений нет), 1 — FAIL,
2 — ошибка использования (нет встречи, ядра, транскрипта или артефактов; ядро не подходит к источнику).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def register(sub: Any, root_parent: Any) -> None:
    command = sub.add_parser(
        "eval",
        help="Оценить разбор встречи по несущему ядру core.json (FULL/PARTIAL/MISSING/DISTORTED, PASS/FAIL)",
        parents=[root_parent],
    )
    command.add_argument("--meeting", required=True, help="id встречи в negotiations/")
    command.add_argument("--core", required=True, help="Путь к core.json (абсолютный, от текущего каталога или от --root)")


def handle(args: Any, root: Path) -> tuple[dict[str, Any], list[str], int] | None:
    if getattr(args, "command", None) != "eval":
        return None
    from .evals import run_eval, summary_lines

    result = run_eval(root, args.meeting, Path(args.core))
    return result, summary_lines(result), 0 if result["verdict"] == "PASS" else 1
