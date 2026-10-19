#!/usr/bin/env python3
"""Единая точка входа движка анализа переговоров.

Использование: na.py [--root R] [--json] <команда> [подкоманда] [параметры]

Корень данных (workspace): --root, иначе переменная NA_WORKSPACE, иначе
текущий каталог. Код плагина и данные пользователя разделены: схемы и шаблоны
ищутся относительно этого файла, а всё остальное — в workspace.

Коды выхода: 0 — успех; 1 — проверка не пройдена (схема, цитаты, целостность);
2 — ошибка предусловия или использования (нет файла, нет --confirm, дубликат).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from na_engine import ENGINE_VERSION  # noqa: E402
from na_engine.common import NaError, resolve_root  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    root_parent = argparse.ArgumentParser(add_help=False)
    # SUPPRESS: общие флаги допустимы и до, и после подкоманды; отсутствие флага у подкоманды не затирает значение
    root_parent.add_argument("--root", dest="root", default=argparse.SUPPRESS, help="Рабочий каталог с данными (по умолчанию NA_WORKSPACE или текущий)")
    root_parent.add_argument("--json", dest="json_output", action="store_true", default=argparse.SUPPRESS, help="Печатать результат в JSON")

    parser = argparse.ArgumentParser(prog="na.py", description="Движок накопительной системы анализа переговоров", parents=[root_parent])
    parser.add_argument("--version", action="version", version=f"na.py {ENGINE_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    # source
    source = sub.add_parser("source", help="Приём источника: create, gate", parents=[root_parent])
    source_sub = source.add_subparsers(dest="subcommand", required=True)
    create = source_sub.add_parser("create", help="Создать папку встречи и SourcePacket", parents=[root_parent])
    create.add_argument("--meeting", required=True)
    create.add_argument("--transcript", required=True)
    create.add_argument("--note")
    create.add_argument("--provider", default="manual", choices=["manual", "plaud", "wonderscribe", "local_file", "uploaded_transcript", "document"])
    create.add_argument("--captured-at", dest="captured_at")
    create.add_argument("--title")
    create.add_argument("--external-id", dest="external_id", help="Внешний идентификатор у провайдера (без секретных токенов)")
    create.add_argument("--allow-duplicate", action="store_true")
    gate = source_sub.add_parser("gate", help="Записать решение шлюза согласия", parents=[root_parent])
    gate.add_argument("--meeting", required=True)
    gate.add_argument("--consent", required=True, choices=["yes", "no"])
    gate.add_argument("--masking", required=True, choices=["none", "initials", "pseudonyms", "full"])
    gate.add_argument("--sensitivity", required=True, choices=["public", "internal", "confidential", "regulated"])
    gate.add_argument("--routes", required=True, choices=["local_only", "local_and_cloud_llm"])
    gate.add_argument("--retain-source", dest="retain_source", choices=["yes", "no"])

    # speakers
    speakers = sub.add_parser("speakers", help="Карта спикеров: set", parents=[root_parent])
    speakers_sub = speakers.add_subparsers(dest="subcommand", required=True)
    speakers_set = speakers_sub.add_parser("set", parents=[root_parent])
    speakers_set.add_argument("--meeting", required=True)
    speakers_set.add_argument("--map", required=True, help='"Speaker 1=Имя (роль);Speaker 2=Имя"')

    # validate / report / followup / manifest
    for name, help_text in (("validate", "Проверить 11 JSON, цитаты и meeting_id"), ("report", "Собрать report_auto.md с блоком «Один экран»"), ("followup", "Собрать follow-up пакет (draft_not_sent)"), ("manifest", "Записать run_manifest.json")):
        item = sub.add_parser(name, help=help_text, parents=[root_parent])
        item.add_argument("--meeting", required=True)

    # knowledge
    knowledge = sub.add_parser("knowledge", help="База знаний: diff, apply", parents=[root_parent])
    knowledge_sub = knowledge.add_subparsers(dest="subcommand", required=True)
    kdiff = knowledge_sub.add_parser("diff", help="Показать diff предложения, ничего не применяя", parents=[root_parent])
    kdiff.add_argument("--meeting", required=True)
    kapply = knowledge_sub.add_parser("apply", help="Применить предложение (только с --confirm)", parents=[root_parent])
    kapply.add_argument("--meeting", required=True)
    kapply.add_argument("--confirm", action="store_true")

    # review
    review = sub.add_parser("review", help="Очередь проверки: queue, record, confirm", parents=[root_parent])
    review_sub = review.add_subparsers(dest="subcommand", required=True)
    review_sub.add_parser("queue", help="Пересобрать очередь", parents=[root_parent])
    record = review_sub.add_parser("record", help="Записать решение пользователя по пункту", parents=[root_parent])
    record.add_argument("--item", required=True)
    record.add_argument("--decision", required=True)
    record.add_argument("--status", default="closed", choices=["closed", "deferred"])
    confirm = review_sub.add_parser("confirm", help="Зафиксировать статус ручной проверки встречи", parents=[root_parent])
    confirm.add_argument("--meeting", required=True)
    confirm.add_argument("--status", default="confirmed", choices=["confirmed", "confirmed_with_open_items", "pending"])

    # pipeline
    refresh_all = sub.add_parser("refresh-all", help="Пересобрать все производные представления", parents=[root_parent])
    refresh_all.add_argument("--force", action="store_true")
    refresh = sub.add_parser("refresh", help="Обновить одну встречу", parents=[root_parent])
    refresh.add_argument("--meeting", required=True)
    refresh.add_argument("--force", action="store_true")
    status = sub.add_parser("status", help="Состояние конвейера", parents=[root_parent])
    status.add_argument("--meeting")
    control = sub.add_parser("control-center", help="Собрать system/control_center.{md,json}", parents=[root_parent])
    control.add_argument("--as-of", dest="as_of", help="Дата снимка YYYY-MM-DD")
    search = sub.add_parser("search", help="Полнотекстовый поиск", parents=[root_parent])
    search.add_argument("query")
    search.add_argument("--kind")
    search.add_argument("--meeting")
    search.add_argument("--person")
    search.add_argument("--topic")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--any", dest="match_any", action="store_true", help="Достаточно любого слова")

    # bundle
    bundle = sub.add_parser("bundle", help="Упаковка артефактов: pack, unpack", parents=[root_parent])
    bundle_sub = bundle.add_subparsers(dest="subcommand", required=True)
    pack = bundle_sub.add_parser("pack", parents=[root_parent])
    pack.add_argument("--meeting", required=True)
    unpack = bundle_sub.add_parser("unpack", parents=[root_parent])
    unpack.add_argument("--meeting", required=True)
    unpack.add_argument("--force", action="store_true")

    # audio (опционально)
    audio = sub.add_parser("audio", help="Опциональный акустический слой (нужны numpy, soundfile, librosa)", parents=[root_parent])
    audio.add_argument("--meeting")
    audio.add_argument("--wav")
    audio.add_argument("--segments")
    audio.add_argument("--check", action="store_true", help="Только проверить наличие зависимостей")
    for extension in _cli_extensions():
        extension.register(sub, root_parent)
    return parser


def _cli_extensions() -> list:
    """Модули na_engine/cli_*.py с функциями register(sub, root_parent) и handle(args, root).

    handle возвращает (результат, строки, код) или None, если команда не его.
    """
    import importlib
    import pkgutil

    import na_engine

    modules = []
    for info in sorted(pkgutil.iter_modules(na_engine.__path__), key=lambda item: item.name):
        if info.name.startswith("cli_"):
            modules.append(importlib.import_module(f"na_engine.{info.name}"))
    return modules


def dispatch(args: argparse.Namespace, root: Path) -> tuple[dict, list[str], int]:
    """Вернуть (результат, строки для человека, код выхода)."""
    command = args.command
    if command == "source" and args.subcommand == "create":
        from na_engine.source import create_source

        result = create_source(root, args.meeting, Path(args.transcript), Path(args.note) if args.note else None, args.provider, args.captured_at, args.title, args.allow_duplicate, args.external_id)
        lines = [
            f"Встреча {result['meeting_id']} создана в {result['meeting_dir']}; источник {result['source_id']}.",
            f"Сегментов: {result['segments']}; таймкоды: {'есть' if result['with_timestamps'] else 'нет'}; метки спикеров: {', '.join(result['speaker_labels']) or 'не обнаружены'}.",
            "Статус: registered. Следующий шаг — шлюз согласия: na.py source gate --meeting … --consent yes|no --masking … --sensitivity … --routes …",
        ]
        if result["duplicate_of"]:
            lines.append(f"Внимание: зарегистрировано как дубликат встречи {result['duplicate_of']} (--allow-duplicate).")
        return result, lines, 0
    if command == "source" and args.subcommand == "gate":
        from na_engine.source import record_gate

        result = record_gate(root, args.meeting, args.consent, args.masking, args.sensitivity, args.routes, args.retain_source)
        return result, [f"Шлюз записан: processing_status={result['processing_status']}.", result["note"]], 0
    if command == "speakers":
        from na_engine.source import set_speakers

        result = set_speakers(root, args.meeting, args.map)
        lines = ["Карта спикеров подтверждена пользователем:"] + [f"  {item['label']} → {item['person_name']}{' (' + item['role'] + ')' if item.get('role') else ''}" for item in result["speakers"]]
        if result["labels_without_mapping"]:
            lines.append(f"Без сопоставления: {', '.join(result['labels_without_mapping'])}")
        return result, lines, 0
    if command == "validate":
        from na_engine.validate import run_validate, summary_lines

        result = run_validate(root, args.meeting)
        return result, summary_lines(result), 0 if result["valid"] else 1
    if command == "report":
        from na_engine.report import run_report

        result = run_report(root, args.meeting)
        validation = result["report_validation"]
        lines = [f"report_auto.md собран; проверка полноты: {'пройдена' if validation['valid'] else 'НЕ пройдена'} (заголовков: {validation['required_headings']}, участников: {validation['participants_checked']}, обязательств: {validation['commitments_checked']}, рисков: {validation['risks_checked']})."]
        lines += [f"  ошибка: {item}" for item in validation["errors"]]
        return result, lines, 0 if result["valid"] else 1
    if command == "followup":
        from na_engine.followup import run_followup

        result = run_followup(root, args.meeting)
        return result, [f"Follow-up пакет собран, статус {result['delivery_status']}: " + ", ".join(result["outputs"]), "Ничего не отправлено: отправка не входит в конвейер."], 0
    if command == "manifest":
        from na_engine.pipeline import summary_lines_status, write_manifest
        from na_engine.common import meeting_dir

        result = write_manifest(root, meeting_dir(root, args.meeting))
        return result, summary_lines_status(result), 0
    if command == "knowledge" and args.subcommand == "diff":
        from na_engine.knowledge import run_diff

        result = run_diff(root, args.meeting)
        changed_files = [item for item in result["files"] if not item["unchanged"]]
        changed_records = [item for item in result["records"] if not item["unchanged"]]
        lines = [f"Diff записан в negotiations/{args.meeting}/knowledge_diff.md; база НЕ изменена.", f"Файлов к изменению: {len(changed_files)}; записей реестров: {len(changed_records)}; ошибок предусловий: {len(result['errors'])}."]
        lines += [f"  ошибка: {item}" for item in result["errors"]]
        if result["valid"]:
            lines.append("Применить после проверки diff: na.py knowledge apply --meeting … --confirm")
        return result, lines, 0 if result["valid"] else 1
    if command == "knowledge" and args.subcommand == "apply":
        from na_engine.knowledge import run_apply

        result = run_apply(root, args.meeting, args.confirm)
        if result["status"] == "already_applied":
            return result, [result["note"]], 0
        return result, [f"Предложение применено по подтверждению пользователя: файлов {len(result['files_written'])}, реестров {len(result['registries_written'])}, записей {len(result['records'])}, событий журнала {result['ledger_events_appended']}."], 0
    if command == "review" and args.subcommand == "queue":
        from na_engine.review import run_queue

        result = run_queue(root)
        summary = result["summary"]
        return result, [f"Очередь пересобрана: открыто {summary['open']}, закрыто {summary['closed']}, отложено {summary['deferred']}, всего {summary['total']}."], 0
    if command == "review" and args.subcommand == "record":
        from na_engine.review import record_decision

        result = record_decision(root, args.item, args.decision, args.status)
        return result, [f"Решение {result['decision']['decision_id']} по пункту {args.item} записано (status={args.status}, source=user_confirmation).", f"Очередь: открыто {result['queue']['open']}, всего {result['queue']['total']}."], 0
    if command == "review" and args.subcommand == "confirm":
        from na_engine.source import confirm_review

        result = confirm_review(root, args.meeting, args.status)
        return result, [f"review_status={result['review_status']} для {args.meeting}."], 0
    if command == "refresh-all":
        from na_engine.pipeline import refresh_all

        result = refresh_all(root, args.force)
        lines = [f"refresh-all: {'все проверки пройдены' if result['valid'] else 'есть незавершённые этапы или ошибки'}; итог в system/pipeline_run.json."]
        for item in result["meetings"]:
            lines.append(f"  {item['meeting_id']}: {item['status']}, {item['overall_status']}; продолжить с: {item['resume_from'] or '—'}; сбоев: {item['failed_actions']}")
        for key in ("review_queue", "search_index", "control_center"):
            lines.append(f"  {key}: {'ok' if result[key]['ok'] else 'ошибка: ' + str(result[key].get('error') or result[key].get('result', {}).get('errors'))}")
        ledger = result["event_ledger"]
        lines.append(f"  ledger: sync {'ok' if ledger['sync']['ok'] else 'ошибка'}; проекция {'ok' if ledger['projection'] and ledger['projection']['ok'] else 'ошибка'}; сверка {'ok' if ledger['validation'] and ledger['validation']['ok'] else 'ошибка'}")
        return result, lines, 0 if result["valid"] else 1
    if command == "refresh":
        from na_engine.pipeline import refresh_meeting
        from na_engine.common import meeting_dir

        result = refresh_meeting(root, meeting_dir(root, args.meeting), args.force)
        lines = [f"{result['meeting_id']}: {result['status']}, {result['overall_status']}; продолжить с: {result['resume_from'] or '—'}; сбоев: {result['failed_actions']}"]
        lines += [f"  {item['action']}: {'ok' if item['ok'] else 'ошибка: ' + str(item.get('error') or item.get('result', {}).get('errors'))}" for item in result["actions"]]
        return result, lines, 0 if result["failed_actions"] == 0 else 1
    if command == "status":
        from na_engine.pipeline import status, summary_lines_status

        result = status(root, args.meeting)
        return result, summary_lines_status(result), 0
    if command == "control-center":
        from na_engine.control_center import run_control_center

        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        result = run_control_center(root, as_of)
        summary = result["summary"]
        return result, [f"Контрольный центр собран: встреч {summary['meetings_total']} (готовы {summary['meetings_ready']}), открытых обязательств {summary['open_commitments']}, активных рисков {summary['active_risks']}, ошибок целостности {summary['data_errors']}, предупреждений {summary['data_warnings']}."], 0 if result["valid"] else 1
    if command == "search":
        from na_engine.search import format_results, search

        result = search(root, args.query, args.kind, args.meeting, args.person, args.topic, args.limit, args.match_any)
        return result, format_results(result), 0
    if command == "bundle" and args.subcommand == "pack":
        from na_engine.bundle import pack

        result = pack(root, args.meeting)
        return result, [f"Упаковано {len(result['files'])} файлов в {result['bundle']}."], 0
    if command == "bundle" and args.subcommand == "unpack":
        from na_engine.bundle import unpack

        result = unpack(root, args.meeting, args.force)
        lines = [f"Распаковка: записано {len(result['written'])}, без изменений {len(result['unchanged'])}, пропущено {len(result['skipped'])}."]
        lines += [f"  пропущен {item['path']}: {item['reason']}" for item in result["skipped"]]
        return result, lines, 0 if result["valid"] else 1
    if command == "audio":
        from na_engine.audio_acoustic import dependency_report, run_audio

        if args.check or not (args.meeting and args.wav and args.segments):
            result = dependency_report()
            lines = ["Аудиослой доступен." if result["available"] else "Аудиослой недоступен: отсутствуют " + ", ".join(result["missing"]) + ".", result["note"]]
            if not args.check and not result["missing"]:
                lines.append("Для расчёта укажите --meeting, --wav и --segments.")
            return result, lines, 0 if result["available"] else 2
        result = run_audio(root, args.meeting, Path(args.wav), Path(args.segments))
        return result, [f"Акустические метрики записаны: {result['output']}.", "Метрики не содержат смысловых или клинических выводов."], 0
    for extension in _cli_extensions():
        handled = extension.handle(args, root)
        if handled is not None:
            return handled
    raise NaError(f"Неизвестная команда: {command}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    root = resolve_root(getattr(args, "root", None))
    json_output = bool(getattr(args, "json_output", False))
    try:
        result, lines, code = dispatch(args, root)
    except NaError as exc:
        if json_output:
            print(json.dumps({"valid": False, "error": str(exc), "exit_code": exc.exit_code}, ensure_ascii=False, indent=2))
        else:
            print(f"Ошибка: {exc}", file=sys.stderr)
        return exc.exit_code
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for line in lines:
            print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
