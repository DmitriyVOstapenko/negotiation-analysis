"""Команда `knowledge decide`: решения пользователя по пунктам учёта предложения.

na.py knowledge decide --meeting <id> --item <пункт|№> --decision accept|reject|rewrite|merge [--text …] [--target …]
na.py knowledge decide --meeting <id> --list

Решение дописывается в negotiations/<id>/knowledge_decisions.jsonl (source: user_confirmation).
База знаний не меняется: решение учитывают следующие `knowledge diff` и `knowledge apply`.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .common import NaError


def _subparsers(parser: argparse.ArgumentParser) -> Any:
    return next((action for action in parser._actions if isinstance(action, argparse._SubParsersAction)), None)


def register(sub: Any, root_parent: argparse.ArgumentParser) -> None:
    knowledge = sub.choices.get("knowledge")
    knowledge_sub = _subparsers(knowledge) if knowledge is not None else None
    if knowledge_sub is None:
        return
    decide = knowledge_sub.add_parser(
        "decide",
        help="Решение пользователя по пункту учёта: принять, отклонить, переписать, объединить",
        parents=[root_parent],
    )
    decide.add_argument("--meeting", required=True)
    decide.add_argument("--item", help="Пункт учёта (ref, например risk:r-…) или номер сомнительного пункта из diff")
    decide.add_argument("--decision", choices=["accept", "reject", "rewrite", "merge"])
    decide.add_argument("--text", help="Новая формулировка для rewrite")
    decide.add_argument("--target", help="Запись для merge: <id> | knowledge/….jsonl#<id> | knowledge/….md[#Раздел]")
    decide.add_argument("--list", dest="list_items", action="store_true", help="Показать сомнительные пункты и решения по ним")


def handle(args: argparse.Namespace, root: Path) -> tuple[dict, list[str], int] | None:
    if getattr(args, "command", None) != "knowledge" or getattr(args, "subcommand", None) != "decide":
        return None
    from .accounting import decision_text, list_items, record_decision, target_text

    if args.list_items:
        result = list_items(root, args.meeting)
        summary = result["summary"]
        lines = [f"Учёт {args.meeting}: уверенных {summary['sure']}, сомнительных {summary['doubtful']} (решено {summary['decided']}, ждут решения {len(summary['pending'])})."]
        if result["applied"]:
            lines.append("Предложение уже применено: решения по нему базу не меняют.")
        for item in result["doubtful"]:
            lines.append(f"  {item['number']}. {item['item']} — {item['object']}; предлагается {item['fate']} {target_text(item['target']) if item['target'] else ''}".rstrip())
            lines.append(f"     решение: {decision_text(item['decision'])}")
        lines += [f"  ошибка: {item}" for item in result["errors"]]
        return result, lines, 0 if result["valid"] else 1
    if not args.item or not args.decision:
        raise NaError("Укажите --item и --decision (или --list, чтобы увидеть сомнительные пункты)")
    result = record_decision(root, args.meeting, args.item, args.decision, args.text, args.target)
    decision = result["decision"]
    pending = result["pending"]
    lines = [
        f"Решение по пункту {decision['item']} записано: {decision_text(decision)}.",
        f"Ждут решения сомнительных пунктов: {len(pending)}" + (f" ({', '.join(pending)})" if pending else "") + ".",
        result["note"],
    ]
    return result, lines, 0
