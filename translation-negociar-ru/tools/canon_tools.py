#!/usr/bin/env python3
"""Инструменты перевода канона Negociar (ES → RU).

Команды:
  split     — разбить доменные файлы канона на единицы перевода (преамбула + принципы ###)
              → translation/canon-units/<file>.src.json + translation/titles.src.json
  assemble  — собрать русские md-файлы из translation/canon-units/<file>.ru.json
              и translation/titles.ru.json → skills/negociar/data/canon/<file>
  check     — проверить сборку: 275 принципов, ссылки [[...]] разрешаются, остатки испанского,
              хеши единиц; обновить translation/canon-status.json
Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TR = Path(__file__).resolve().parents[1]      # translation-negociar-ru/
REPO = Path(__file__).resolve().parents[2]    # корень плагина
ROOT = TR
SRC = TR / "canon-src"
UNITS = TR / "canon-units"
OUT = REPO / "skills" / "negociar-ru" / "data" / "canon"
DOMAIN_FILES = [
    "01-fundamentos-lucha-fundamentals-management-fight.md",
    "02-imagen-mundo-picture-world.md",
    "03-dinamica-roles-role-dynamics.md",
    "04-negociacion-posicional-positional-negotiation.md",
    "05-ventas-complejas-complex-sales.md",
    "06-autoridad-distanciamiento-authority-distancing.md",
    "07-tecnologia-social-social-technology.md",
    "08-gestion-conflicto-conflict-management.md",
    "09-productividad-habitos-productivity-habits.md",
    "11-capital-social-social-capital.md",
]
EXPECTED_PRINCIPLES = 275
# частые испанские служебные слова — эвристика остатков испанского в русском тексте
SPANISH_MARKERS = re.compile(
    r"\b(el|la|los|las|de|del|que|para|por|con|una|uno|es|son|como|pero|cuando|porque|"
    r"también|siempre|nunca|entonces|aquí|ahora|hace|tiene|hay|más|muy|sin|sobre|desde|"
    r"hasta|donde|quien|cada|todo|todos|esta|este|esto|esa|ese|eso|sus|tu|usted)\b",
    re.IGNORECASE,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_title(raw: str) -> tuple[str, str]:
    """Заголовок или текст ссылки → (испанская часть, английская часть).
    Форматы источника: «ES / EN», «[ES — EN]» (файл 01), «ES»."""
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    for sep in (" / ", " — "):
        if sep in s:
            es, _, en = s.partition(sep)
            return es.strip(), en.strip()
    return s, ""


def norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def split_file(name: str) -> dict:
    text = (SRC / name).read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(?=### )", text)
    preamble = parts[0]
    units = []
    for i, block in enumerate(parts[1:], start=1):
        first, _, body = block.partition("\n")
        heading = first[4:].strip()
        title_es, title_en = split_title(heading)
        units.append(
            {
                "index": i,
                "heading": heading,
                "title_es": title_es.strip(),
                "title_en": title_en.strip(),
                "body_es": body.rstrip("\n") + "\n",
                "sha256_src": sha(block),
            }
        )
    return {"file": name, "preamble_es": preamble, "units": units, "sha256_file": sha(text)}


def cmd_split(_: argparse.Namespace) -> int:
    UNITS.mkdir(parents=True, exist_ok=True)
    titles = []
    total = 0
    for name in DOMAIN_FILES:
        data = split_file(name)
        (UNITS / f"{name}.src.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        for u in data["units"]:
            titles.append({"file": name, "index": u["index"], "title_es": u["title_es"], "title_en": u["title_en"]})
        total += len(data["units"])
        print(f"{name}: {len(data['units'])} принципов")
    (TR / "titles.src.json").write_text(json.dumps(titles, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Всего принципов: {total} (ожидается {EXPECTED_PRINCIPLES})")
    return 0 if total == EXPECTED_PRINCIPLES else 1


def load_title_map() -> dict[str, tuple[str, str]]:
    """Нормализованная испанская часть заголовка → (русский заголовок, английская часть)."""
    path = TR / "titles.ru.json"
    if not path.exists():
        raise SystemExit("нет translation/titles.ru.json — сначала перевод заголовков")
    items = json.loads(path.read_text(encoding="utf-8"))
    m: dict[str, tuple[str, str]] = {}
    for it in items:
        if not it.get("title_ru"):
            raise SystemExit(f"пустой title_ru: {it}")
        es, en = split_title(it["title_es"])
        en = it.get("title_en") or en
        m[norm_key(es)] = (it["title_ru"].strip(), en.strip())
    return m


def full_heading(ru: str, en: str) -> str:
    return f"{ru} / {en}" if en else ru


LINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]")


def load_link_aliases() -> dict[str, str]:
    """Сокращённые или неточные ссылки источника → точный испанский заголовок (translation/link-aliases.json)."""
    path = TR / "link-aliases.json"
    if not path.exists():
        return {}
    return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if not k.startswith("_")}


LINK_ALIASES: dict[str, str] | None = None


def relink(text: str, tmap: dict[str, str], unresolved: list[str]) -> str:
    global LINK_ALIASES
    if LINK_ALIASES is None:
        LINK_ALIASES = load_link_aliases()

    full_ru = {full_heading(ru, en) for ru, en in tmap.values()}

    def rep(m: re.Match) -> str:
        raw = m.group(1).strip()
        raw = LINK_ALIASES.get(raw, raw)
        if raw in full_ru:  # уже русская ссылка
            return f"[[{raw}]]"
        es, _ = split_title(raw)
        hit = tmap.get(norm_key(LINK_ALIASES.get(es, es)))
        if hit:
            return f"[[{full_heading(*hit)}]]"
        unresolved.append(raw)
        return m.group(0)

    return LINK_RE.sub(rep, text)


def load_exclusions() -> dict[tuple[str, int], dict]:
    path = TR / "exclusions.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(e["file"], int(e["index"])): e for e in data.get("canon", [])}


EXCLUDED_BODY = (
    "*Этот принцип изъят из русской адаптации по решению её автора. "
    "Причина: {reason} Исходный текст — в оригинальном репозитории Negociar; "
    "подробности — в `NOTICE.md`.*\n"
)


def cmd_assemble(args: argparse.Namespace) -> int:
    tmap = load_title_map()
    excluded = load_exclusions()
    OUT.mkdir(parents=True, exist_ok=True)
    problems = 0
    for name in DOMAIN_FILES:
        ru_path = UNITS / f"{name}.ru.json"
        if not ru_path.exists():
            print(f"[пропуск] {name}: нет перевода")
            continue
        src = json.loads((UNITS / f"{name}.src.json").read_text(encoding="utf-8"))
        ru = json.loads(ru_path.read_text(encoding="utf-8"))
        ru_units = {u["index"]: u for u in ru["units"]}
        unresolved: list[str] = []
        lines = [relink(ru.get("preamble_ru") or src["preamble_es"], tmap, unresolved).rstrip("\n"), ""]
        for u in src["units"]:
            r = ru_units.get(u["index"])
            exc = excluded.get((name, u["index"]))
            if exc:
                r = dict(r or {}, body_ru=EXCLUDED_BODY.format(reason=exc["reason"]))
            if not r or not r.get("body_ru", "").strip():
                print(f"  [ошибка] {name} #{u['index']}: нет body_ru")
                problems += 1
                continue
            hit = tmap.get(norm_key(u["title_es"]))
            title_ru, title_en = hit if hit else (r.get("title_ru"), u["title_en"])
            heading = "### " + full_heading(title_ru, title_en or u["title_en"])
            lines.append(heading)
            lines.append(relink(r["body_ru"].rstrip("\n"), tmap, unresolved))
            lines.append("")
        out_text = "\n".join(lines).rstrip("\n") + "\n"
        (OUT / name).write_text(out_text, encoding="utf-8")
        if unresolved:
            problems += len(unresolved)
            print(f"  [ссылки] {name}: не разрешены {len(unresolved)}: {sorted(set(unresolved))[:6]}")
        print(f"{name}: собрано {len(src['units'])} принципов")
    return 1 if problems else 0


def spanish_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", text)
    if not words:
        return 0.0
    return len(SPANISH_MARKERS.findall(text)) / max(1, len(text.split()))


def cmd_check(_: argparse.Namespace) -> int:
    tmap = load_title_map()
    all_titles_ru = {full_heading(ru, en) for ru, en in tmap.values()}
    status = {"files": {}, "principles": 0, "problems": []}
    for name in DOMAIN_FILES:
        path = OUT / name
        if not path.exists():
            status["problems"].append(f"{name}: не собран")
            continue
        text = path.read_text(encoding="utf-8")
        heads = re.findall(r"(?m)^### (.+?)(?: / .*)?$", text)
        n = len(heads)
        status["principles"] += n
        links = [m.group(1).strip() for m in LINK_RE.finditer(text)]
        bad = [l for l in links if l not in all_titles_ru]
        ratio = spanish_ratio(text)
        # остатки испанского: ищем абзацы, где доля испанских служебных слов заметна
        leftovers = []
        for para in text.split("\n"):
            if len(para.split()) >= 8 and len(SPANISH_MARKERS.findall(para)) >= 4:
                leftovers.append(para[:90])
        status["files"][name] = {
            "principles": n,
            "links": len(links),
            "unresolved_links": bad[:10],
            "spanish_marker_ratio": round(ratio, 4),
            "suspect_lines": len(leftovers),
            "sha256": sha(text),
        }
        if bad:
            status["problems"].append(f"{name}: неразрешённых ссылок {len(bad)}")
        if leftovers:
            status["problems"].append(f"{name}: подозрительных строк с испанским {len(leftovers)}")
            for l in leftovers[:3]:
                print(f"  [испанский?] {name}: {l}")
    if status["principles"] != EXPECTED_PRINCIPLES:
        status["problems"].append(f"принципов {status['principles']} вместо {EXPECTED_PRINCIPLES}")
    (TR / "canon-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Принципов: {status['principles']}/{EXPECTED_PRINCIPLES}; файлов: {len(status['files'])}; проблем: {len(status['problems'])}")
    for p in status["problems"]:
        print("  -", p)
    return 1 if status["problems"] else 0


def cmd_merge_parts(args: argparse.Namespace) -> int:
    """Собрать <file>.ru.json из каталога <file>.ru.parts/ (файлы unit-<index>.json или part-*.json со списками)."""
    name = args.file
    src = json.loads((UNITS / f"{name}.src.json").read_text(encoding="utf-8"))
    parts_dir = UNITS / f"{name}.ru.parts"
    if not parts_dir.exists():
        raise SystemExit(f"нет каталога {parts_dir}")
    units: dict[int, dict] = {}
    preamble_ru = None
    for p in sorted(parts_dir.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "preamble_ru" in data and "index" not in data:
            preamble_ru = data["preamble_ru"]
            continue
        items = data if isinstance(data, list) else [data]
        for u in items:
            if "index" not in u or not str(u.get("body_ru", "")).strip():
                print(f"  [пропуск] {p.name}: нет index или body_ru")
                continue
            units[int(u["index"])] = {"index": int(u["index"]), "title_ru": u.get("title_ru", ""), "body_ru": u["body_ru"],
                                       "status": u.get("status", "machine_draft"), "notes": u.get("notes", "")}
    missing = [u["index"] for u in src["units"] if u["index"] not in units]
    out = {"file": name, "preamble_ru": preamble_ru, "glossary_version": "draft-1", "method": "llm_builtin",
           "units": [units[i] for i in sorted(units)]}
    (UNITS / f"{name}.ru.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: собрано единиц {len(units)}/{len(src['units'])}; преамбула: {'есть' if preamble_ru else 'нет'}; не хватает: {missing[:10]}")
    return 1 if missing or not preamble_ru else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    mp = sub.add_parser("merge-parts"); mp.add_argument("file"); mp.set_defaults(fn=cmd_merge_parts)
    sub.add_parser("split").set_defaults(fn=cmd_split)
    sub.add_parser("assemble").set_defaults(fn=cmd_assemble)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
