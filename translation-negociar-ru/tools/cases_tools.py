#!/usr/bin/env python3
"""Инструменты перевода библиотеки случаев Negociar (casos.json, ES → RU).

Команды:
  export   --size N          — нарезать непереведённые случаи на пакеты translation/cases/batch-NNN.src.json
  import   <batch.ru.json>   — проверить и принять переведённый пакет (id+n совпадают, поля непустые,
                               остатки испанского, хеши) → translation/cases/accepted/batch-NNN.ru.json
  status                     — сводка: переведено / осталось, по tier и сфере
  assemble                   — собрать skills/negociar/data/casos.json: title/body/domain заменены русскими
                               там, где перевод принят; остальные записи — исходные (помечены status)
  titles-export / titles-import <file> — отдельный быстрый контур перевода только заголовков
Только стандартная библиотека. Единица перевода — пара id+n; она не меняется.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

TR = Path(__file__).resolve().parents[1]      # translation-negociar-ru/
REPO = Path(__file__).resolve().parents[2]    # корень плагина
ROOT = TR
SRC = TR / "cases" / "casos.src.json"
BATCHES = TR / "cases"
ACCEPTED = BATCHES / "accepted"
OUT = REPO / "skills" / "negociar-ru" / "data" / "casos.json"
STATUS = TR / "cases-status.json"
TITLES_RU = TR / "cases" / "titles.ru.json"
DOMAIN_RU = {
    "D1": "D1 Основы управленческой борьбы",
    "D2": "D2 Картина мира",
    "D3": "D3 Динамика ролей",
    "D4": "D4 Позиционные переговоры",
    "D5": "D5 Сложные продажи B2B",
    "D6": "D6 Авторитет и лидерство",
    "D7": "D7 Влияние и убеждение",
    "D8": "D8 Конфликт и дисциплина",
    "D9": "D9 Продуктивность и привычки",
    "D11": "D11 Социальный капитал",
}
SPANISH_MARKERS = re.compile(
    r"\b(el|la|los|las|de|del|que|para|por|con|una|uno|es|son|como|pero|cuando|porque|"
    r"también|siempre|nunca|entonces|ahora|tiene|hay|más|muy|sin|sobre|desde|hasta|donde|"
    r"cada|todo|todos|esta|este|esto|sus|usted)\b",
    re.IGNORECASE,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def key(c: dict) -> str:
    return f"{c['id']}:{c['n']}"


def load_src() -> list[dict]:
    return json.loads(SRC.read_text(encoding="utf-8-sig"))


def accepted_map() -> dict[str, dict]:
    m: dict[str, dict] = {}
    if ACCEPTED.exists():
        for p in sorted(ACCEPTED.glob("batch-*.ru.json")):
            for c in json.loads(p.read_text(encoding="utf-8")):
                m[key(c)] = c
    return m


def cmd_export(args: argparse.Namespace) -> int:
    src = load_src()
    done = accepted_map()
    BATCHES.mkdir(parents=True, exist_ok=True)
    pending = [c for c in src if key(c) not in done]
    existing = sorted(BATCHES.glob("batch-*.src.json"))
    nxt = (max(int(p.name[6:9]) for p in existing) + 1) if existing else 1
    # не экспортировать повторно то, что уже лежит в непринятых пакетах
    queued = set()
    for p in existing:
        if not (ACCEPTED / p.name.replace(".src.json", ".ru.json")).exists():
            for c in json.loads(p.read_text(encoding="utf-8")):
                queued.add(key(c))
    pending = [c for c in pending if key(c) not in queued]
    if getattr(args, "order", "source") == "priority":
        # сначала уровень A с самым развёрнутым разбором (peso), затем B, C
        tier_rank = {"A": 0, "B": 1, "C": 2}
        pending.sort(key=lambda c: (tier_rank.get(c.get("tier"), 3), -(c.get("peso") or 0)))
    exc_path = TR / "exclusions.json"
    if exc_path.exists():
        ex = {f"{e['id']}:{e['n']}" for e in json.loads(exc_path.read_text(encoding="utf-8")).get("cases", [])}
        pending = [c for c in pending if key(c) not in ex]
    size = args.size
    made = 0
    for i in range(0, len(pending), size):
        chunk = pending[i : i + size]
        items = [
            {"id": c["id"], "n": c["n"], "domain": c["domain"], "tier": c["tier"], "sphere": c["sphere"],
             "title": c["title"], "body": c["body"], "sha256_src": sha(c["title"] + "\n" + c["body"] + "\n" + c["domain"])}
            for c in chunk
        ]
        p = BATCHES / f"batch-{nxt:03d}.src.json"
        p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        words = sum(len(c["body"].split()) for c in chunk)
        print(f"{p.name}: {len(chunk)} случаев, ~{words} слов")
        nxt += 1
        made += 1
        if args.max_batches and made >= args.max_batches:
            break
    print(f"Пакетов создано: {made}; осталось непереведённых: {len(pending)}")
    return 0


def validate_batch(items: list[dict], src_by_key: dict[str, dict]) -> list[str]:
    errs = []
    for c in items:
        k = f"{c.get('id')}:{c.get('n')}"
        s = src_by_key.get(k)
        if not s:
            errs.append(f"{k}: нет в исходнике")
            continue
        for f in ("title_ru", "body_ru"):
            if not str(c.get(f, "")).strip():
                errs.append(f"{k}: пустое {f}")
        body = c.get("body_ru", "")
        if len(body.split()) < 0.4 * len(s["body"].split()):
            errs.append(f"{k}: body_ru слишком короткий ({len(body.split())} слов против {len(s['body'].split())})")
        sus = [ln for ln in body.split("\n") if len(ln.split()) >= 8 and len(SPANISH_MARKERS.findall(ln)) >= 4]
        if sus:
            errs.append(f"{k}: похоже на остаток испанского: {sus[0][:70]}")
        if c.get("sha256_src") and c["sha256_src"] != sha(s["title"] + "\n" + s["body"] + "\n" + s["domain"]):
            errs.append(f"{k}: sha256_src не совпадает с исходником")
        dom = (s["domain"].split() or ["?"])[0]
        if dom not in DOMAIN_RU:
            errs.append(f"{k}: неизвестный домен {dom}")
    return errs


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.file)
    items = json.loads(path.read_text(encoding="utf-8"))
    src_by_key = {key(c): c for c in load_src()}
    errs = validate_batch(items, src_by_key)
    if errs:
        print(f"ОТКАЗ: {len(errs)} ошибок")
        for e in errs[:20]:
            print("  -", e)
        return 1
    ACCEPTED.mkdir(parents=True, exist_ok=True)
    out = ACCEPTED / path.name
    cleaned = []
    for c in items:
        s = src_by_key[key(c)]
        cleaned.append(
            {"id": c["id"], "n": c["n"], "title_ru": c["title_ru"].strip(), "body_ru": c["body_ru"].rstrip() + "\n",
             "domain_ru": DOMAIN_RU[s["domain"].split()[0]], "sha256_src": sha(s["title"] + "\n" + s["body"] + "\n" + s["domain"]),
             "sha256_ru": sha(c["title_ru"].strip() + "\n" + c["body_ru"].rstrip() + "\n"),
             "status": c.get("status", "machine_draft"), "method": c.get("method", "llm_builtin"),
             "glossary_version": c.get("glossary_version", "draft-1")}
        )
    out.write_text(json.dumps(cleaned, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Принято: {out.name}, случаев {len(cleaned)}")
    return 0


STATUS_ORDER = ["untranslated", "machine_draft", "terminology_checked", "reviewed", "approved"]


def corpus_status(src: list[dict], done: dict[str, dict]) -> str:
    """Статус корпуса = минимальный статус обязательных элементов (непереведённый случай → untranslated)."""
    worst = len(STATUS_ORDER) - 1
    for c in src:
        st = done.get(key(c), {}).get("status", "untranslated")
        worst = min(worst, STATUS_ORDER.index(st) if st in STATUS_ORDER else 0)
    return STATUS_ORDER[worst]


def cmd_status(_: argparse.Namespace) -> int:
    src = load_src()
    done = accepted_map()
    total = len(src)
    n_done = sum(1 for c in src if key(c) in done)
    by_tier = Counter(c["tier"] for c in src if key(c) in done)
    by_sphere = Counter(c["sphere"] for c in src if key(c) in done)
    words_done = sum(len(c["body"].split()) for c in src if key(c) in done)
    words_total = sum(len(c["body"].split()) for c in src)
    titles = json.loads(TITLES_RU.read_text(encoding="utf-8")) if TITLES_RU.exists() else []
    st = {"total": total, "translated": n_done, "remaining": total - n_done, "by_tier": dict(by_tier),
          "by_sphere": dict(by_sphere), "words_translated": words_done, "words_total": words_total,
          "titles_translated": len(titles), "corpus_status": corpus_status(src, done)}
    STATUS.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0


def cmd_assemble(_: argparse.Namespace) -> int:
    src = load_src()
    done = accepted_map()
    titles = {}
    if TITLES_RU.exists():
        titles = {f"{t['id']}:{t['n']}": t["title_ru"] for t in json.loads(TITLES_RU.read_text(encoding="utf-8")) if t.get("title_ru")}
    exc_path = TR / "exclusions.json"
    excluded = {}
    if exc_path.exists():
        excluded = {f"{e['id']}:{e['n']}": e for e in json.loads(exc_path.read_text(encoding="utf-8")).get("cases", [])}
    out = []
    n_full = n_title = n_exc = 0
    for c in src:
        k = key(c)
        d = dict(c)
        if k in excluded:
            d["title"] = "Изъято из русской адаптации"
            d["body"] = ("*Этот случай изъят из русской адаптации по решению её автора. Причина: "
                         + excluded[k]["reason"] + " Исходный текст — в оригинальном репозитории Negociar; "
                         "подробности — в `NOTICE.md`.*\n")
            d["domain"] = DOMAIN_RU.get(c["domain"].split()[0], c["domain"])
            d["title_es"] = c["title"]
            d["translation_status"] = "excluded"
            d["peso"] = 0
            n_exc += 1
            out.append(d)
            continue
        if k in done:
            r = done[k]
            d["title"], d["body"], d["domain"] = r["title_ru"], r["body_ru"], r["domain_ru"]
            d["title_es"] = c["title"]
            d["translation_status"] = r.get("status", "machine_draft")
            n_full += 1
        elif k in titles:
            d["title"] = titles[k]
            d["title_es"] = c["title"]
            d["domain"] = DOMAIN_RU.get(c["domain"].split()[0], c["domain"])
            d["translation_status"] = "title_only"
            n_title += 1
        else:
            d["translation_status"] = "untranslated"
        out.append(d)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    assert len(out) == len(src) == len({key(c) for c in out})
    print(f"casos.json: записей {len(out)}; полный перевод {n_full}; только заголовок {n_title}; изъято {n_exc}; без перевода {len(out)-n_full-n_title-n_exc}")
    return 0


def cmd_titles_export(_: argparse.Namespace) -> int:
    src = load_src()
    items = [{"id": c["id"], "n": c["n"], "domain": c["domain"], "title_es": c["title"], "title_ru": ""} for c in src]
    p = BATCHES / "titles.src.json"
    BATCHES.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{p.name}: {len(items)} заголовков")
    return 0


def cmd_titles_import(args: argparse.Namespace) -> int:
    items = json.loads(Path(args.file).read_text(encoding="utf-8"))
    src_by_key = {key(c): c for c in load_src()}
    errs = [f"{it.get('id')}:{it.get('n')}" for it in items if f"{it.get('id')}:{it.get('n')}" not in src_by_key or not str(it.get("title_ru", "")).strip()]
    if errs or len(items) != len(src_by_key):
        print(f"ОТКАЗ: записей {len(items)}/{len(src_by_key)}, ошибок {len(errs)}: {errs[:5]}")
        return 1
    TITLES_RU.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Заголовки приняты: {len(items)}")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    """Собрать translation/cases/<batch>.ru.json из каталога translation/cases/work/<batch>/*.json и сразу импортировать."""
    name = args.batch
    work = BATCHES / "work" / name
    src_items = json.loads((BATCHES / f"{name}.src.json").read_text(encoding="utf-8"))
    want = {f"{c['id']}:{c['n']}" for c in src_items}
    got: dict[str, dict] = {}
    for p in sorted(work.glob("*.json")) if work.exists() else []:
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in (data if isinstance(data, list) else [data]):
            k = f"{c.get('id')}:{c.get('n')}"
            if k in want:
                got[k] = c
    missing = sorted(want - set(got))
    out = BATCHES / f"{name}.ru.json"
    out.write_text(json.dumps(list(got.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: собрано {len(got)}/{len(want)}; не хватает: {missing[:8]}")
    if missing:
        return 1
    rc = cmd_import(argparse.Namespace(file=str(out)))
    if rc == 0:
        out.unlink(missing_ok=True)  # каноническая копия — в accepted/
    return rc


def cmd_titles_sync(_: argparse.Namespace) -> int:
    """Перенести в titles.ru.json заголовки, уточнённые переводчиками в принятых пакетах."""
    if not TITLES_RU.exists():
        print("нет titles.ru.json"); return 1
    done = accepted_map()
    items = json.loads(TITLES_RU.read_text(encoding="utf-8"))
    n = 0
    for it in items:
        r = done.get(f"{it['id']}:{it['n']}")
        if r and r.get("title_ru") and r["title_ru"] != it.get("title_ru"):
            print(f"  {it['id']}:{it['n']}: {it.get('title_ru')} → {r['title_ru']}")
            it["title_ru"] = r["title_ru"]; n += 1
    TITLES_RU.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Синхронизировано заголовков: {n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("pack"); pk.add_argument("batch"); pk.set_defaults(fn=cmd_pack)
    e = sub.add_parser("export"); e.add_argument("--size", type=int, default=40); e.add_argument("--max-batches", type=int, default=0); e.add_argument("--order", choices=["source", "priority"], default="source"); e.set_defaults(fn=cmd_export)
    i = sub.add_parser("import"); i.add_argument("file"); i.set_defaults(fn=cmd_import)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("assemble").set_defaults(fn=cmd_assemble)
    sub.add_parser("titles-sync").set_defaults(fn=cmd_titles_sync)
    sub.add_parser("titles-export").set_defaults(fn=cmd_titles_export)
    ti = sub.add_parser("titles-import"); ti.add_argument("file"); ti.set_defaults(fn=cmd_titles_import)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
