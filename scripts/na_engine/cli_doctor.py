"""Команда na.py doctor — самопроверка установки и рабочей папки (только чтение, без сети)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def register(sub: Any, root_parent: Any) -> None:
    doctor = sub.add_parser("doctor", help="Самопроверка: Python, паспорт сборки, хуки, агенты, рабочая папка, соседние плагины", parents=[root_parent])
    doctor.add_argument("--probe", action="store_true", help="Проверить запуском, какие команды Python работают (py -3, python3, python)")


def handle(args: Any, root: Path) -> tuple[dict[str, Any], list[str], int] | None:
    if getattr(args, "command", None) != "doctor":
        return None
    from .doctor import doctor_lines, run_doctor

    result = run_doctor(root, probe=args.probe)
    return result, doctor_lines(result), 1 if result["errors"] else 0
