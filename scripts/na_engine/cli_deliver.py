"""Команды доставки себе: na.py deliver setup | telegram-link | status | preview | send | record | log."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .common import NaError


def register(sub: Any, root_parent: Any) -> None:
    deliver = sub.add_parser("deliver", help="Отправка материалов себе: Telegram (свой бот) и почта (через Gmail)", parents=[root_parent])
    dsub = deliver.add_subparsers(dest="deliver_command", required=True)

    setup = dsub.add_parser("setup", help="Задать адрес почты и ключ бота для отправки себе", parents=[root_parent])
    setup.add_argument("--email", help="Ваш адрес почты; письма уходят только на него")
    setup.add_argument("--telegram-token-stdin", action="store_true", help="Прочитать ключ бота из стандартного ввода и сохранить в delivery/secrets/")
    setup.add_argument("--forget-telegram", action="store_true", help="Отвязать чат и очистить ключ в рабочей папке")
    setup.add_argument("--forget-email", action="store_true", help="Забыть адрес почты")

    link = dsub.add_parser("telegram-link", help="Найти ваш личный чат с ботом и привязать его (с --chat и --confirm)", parents=[root_parent])
    link.add_argument("--chat", type=int, help="id личного чата из списка кандидатов")
    link.add_argument("--confirm", action="store_true")

    dsub.add_parser("status", help="Настройки, ожидающие показы, последние отправки", parents=[root_parent])

    preview = dsub.add_parser("preview", help="Показ перед отправкой: кому, что, сколько частей, отпечаток", parents=[root_parent])
    preview.add_argument("--file", required=True)
    preview.add_argument("--channel", required=True, choices=["telegram", "email"])
    preview.add_argument("--mode", default="auto", choices=["auto", "text", "file"], help="Telegram: текстом, файлом или сам выберет")

    send = dsub.add_parser("send", help="Отправить себе в Telegram показанное (только с --confirm)", parents=[root_parent])
    send.add_argument("--file", required=True)
    send.add_argument("--channel", default="telegram", choices=["telegram", "email"])
    send.add_argument("--confirm", action="store_true")
    send.add_argument("--again", action="store_true", help="Ещё одна копия уже отправленного")
    send.add_argument("--resend-unknown", action="store_true", help="Повтор после неизвестного исхода — когда вы проверили чат")

    rec = dsub.add_parser("record", help="Записать исход письма себе (после действия подключения Gmail)", parents=[root_parent])
    rec.add_argument("--file", required=True)
    rec.add_argument("--channel", default="email", choices=["telegram", "email"])
    rec.add_argument("--status", required=True, choices=["draft", "sent", "failed", "unknown"])
    rec.add_argument("--ref", help="id черновика или письма")
    rec.add_argument("--again", action="store_true")

    log = dsub.add_parser("log", help="Журнал отправок", parents=[root_parent])
    log.add_argument("--limit", type=int, default=20)


def _describe(entry: dict[str, Any]) -> str:
    target = entry.get("label") or entry.get("address")
    parts = f", частей {entry.get('parts_sent')}/{entry.get('parts')}" if entry.get("channel") == "telegram" and entry.get("mode") == "text" else ""
    return f"{entry.get('at')} · {entry.get('channel')} → себе ({target}) · {entry.get('file')} · {entry.get('status')}{parts}"


def handle(args: Any, root: Path) -> tuple[dict[str, Any], list[str], int] | None:
    if getattr(args, "command", None) != "deliver":
        return None
    from . import delivery

    command = args.deliver_command
    if command == "setup":
        token = sys.stdin.readline() if args.telegram_token_stdin else None
        if args.telegram_token_stdin and not (token or "").strip():
            raise NaError("Ключ бота не передан на стандартный ввод; ничего не записано")
        result = delivery.setup(root, args.email, token, args.forget_telegram, args.forget_email)
        lines = ["Настройки доставки себе обновлены:"] + [f"  — {item}" for item in result["changes"]]
        return result, lines, 0
    if command == "telegram-link":
        result = delivery.telegram_link(root, args.chat, args.confirm)
        if result["linked"]:
            linked = result["linked"]
            return result, [f"Привязан личный чат: {linked.get('name') or ''} {('@' + linked['username']) if linked.get('username') else ''} (id {linked['chat_id']}), бот {result['bot']}. Отправка — только сюда."], 0
        lines = [f"Бот: {result['bot'] or 'не определён'}."]
        if not result["candidates"]:
            lines.append("Личных чатов с ботом пока нет: откройте бота в Telegram со своего аккаунта, нажмите «Старт» и повторите.")
        else:
            lines.append("Личные чаты, писавшие боту:")
            lines += [f"  — id {item['chat_id']}: {item.get('name') or ''} {('@' + item['username']) if item.get('username') else ''}" for item in result["candidates"]]
            lines.append("Привязать свой: deliver telegram-link --chat <id> --confirm")
        return result, lines, 0
    if command == "status":
        result = delivery.status(root)
        config = result["config"]
        telegram = config.get("telegram") or {}
        lines = [
            f"Почта себе: {config.get('email_self') or 'не задана'}.",
            f"Telegram себе: {(telegram.get('name') or telegram.get('chat_id')) if telegram else 'чат не привязан'}; ключ бота: {config['telegram_token']}.",
            f"Ожидают отправки после показа: {len(result['pending'])}.",
        ]
        if result["recent"]:
            lines.append("Последние отправки:")
            lines += [f"  {_describe(item)}" for item in result["recent"]]
        return result, lines, 0
    if command == "preview":
        result = delivery.preview(root, args.file, args.channel, args.mode)
        target = result.get("label") or result.get("address")
        how = {"text": f"текстом, сообщений: {result['parts']}", "file": "файлом с подписью"}.get(result["mode"], result["mode"])
        lines = [
            f"Показ: {result['file']} → себе в {('Telegram' if args.channel == 'telegram' else 'почту')} ({target}), {how}.",
            f"Размер: {result['bytes']} байт; отпечаток {result['sha256'][:16]}.",
        ]
        if args.channel == "email":
            lines.append(f"Тема письма: {result.get('subject')}. Письмо создаёт подключение Gmail; адресат — только {result['address']}.")
        for note in result["notes"]:
            lines.append(f"Внимание: {note}.")
        previous = result.get("previous")
        if previous:
            lines.append(f"Этот же текст уже был: {previous.get('at')} — {previous.get('status')}.")
        lines.append("Отправка — после вашего «отправить»: deliver send --confirm (Telegram) или действие Gmail и deliver record (почта).")
        return result, lines, 0
    if command == "send":
        entry = delivery.send(root, args.file, args.channel, args.confirm, args.again, args.resend_unknown)
        code = 0 if entry["status"] == "sent" else 1
        lines = [_describe(entry)]
        if entry.get("error"):
            lines.append(f"Причина: {entry['error']}")
        if entry["status"] in ("unknown", "partial"):
            lines.append("Исход неизвестен или неполон: проверьте чат. Повтор того же текста — только с --resend-unknown.")
        return entry, lines, code
    if command == "record":
        entry = delivery.record(root, args.file, args.channel, args.status, args.ref, args.again)
        return entry, [_describe(entry)], 0
    if command == "log":
        records = delivery.journal(root, args.limit)
        return {"records": records}, [_describe(item) for item in records] or ["Отправок ещё не было."], 0
    raise NaError(f"Неизвестная подкоманда deliver: {command}")
