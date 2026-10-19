#!/usr/bin/env python3
# MIT — Copyright (c) 2026 Artur Galiev (исходный скрипт buscar.mjs, см. лицензию исходного
# репозитория arturgaliev3099-ux/realmanagement, раздел 2 «Código — MIT»).
# Изменения адаптации negociar-ru (Copyright (c) 2026 Dmitriy Ostapenko, MIT): перенос с Node.js
# на Python (только стандартная библиотека), сообщения на русском, поиск по русским словам
# в любой форме (отсечение окончаний).
"""buscar.py — детерминированный поиск случаев для навыка negociar-ru.

Каждый случай: {id, date, dir, title, body, domain, tier, sphere, n, peso}. Глобальный номер =
индекс + 1. Данные (casos.json и canon/) находятся сами — сначала через CLAUDE_PLUGIN_ROOT
(установленный плагин), затем относительно скрипта или текущего каталога.

Использование:
  python3 buscar.py <слова...> [--dom D4[,D5]] [--tier A] [--sphere pareja] [--n 6]
  python3 buscar.py --show <N|N,M,...>   → случай(и) целиком
  python3 buscar.py --canon <Dn>         → ПУТЬ к файлу канона домена
  python3 buscar.py --canon-dir          → путь к каталогу canon/
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT")
DATA_DIRS = [
    Path(ROOT) / "skills" / "negociar-ru" / "data" if ROOT else None,  # установленный плагин
    Path(ROOT) / "data" if ROOT else None,
    HERE.parent / "data",                                              # skills/negociar-ru/data
    Path.cwd(),
    HERE.parents[2] if len(HERE.parents) > 2 else None,
]
DATA = next((d for d in DATA_DIRS if d and (d / "casos.json").exists()), None)
TIER_BOOST = {"A": 1.15, "B": 1.0, "C": 0.7}
FLAGS = ["--dom", "--n", "--tier", "--sphere", "--show", "--canon"]
STEM_RE = re.compile(
    r"(иями|ями|ами|ого|его|ому|ему|ыми|ими|ость|ости|ение|ения|ений|ению|ании|ание|ания|ий|ый|ой|ая|яя|"
    r"ое|ее|ые|ие|ую|юю|ов|ев|ах|ях|ам|ям|ом|ем|ию|ия|ии|а|я|о|е|ы|и|у|ю|ь)$"
)


def norm(s: str) -> str:
    """Нижний регистр без диакритики: «negociacion» = «negociación», «е» = «ё»."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def stem_ru(t: str) -> str:
    """Грубая основа русского слова: «извинения» находит «извинений», «фиксация» — «фиксацию»."""
    if not re.search(r"[а-я]", t) or len(t) < 5:
        return t
    s = STEM_RE.sub("", t)
    return s if len(s) >= 4 else t[:4]


def dom_of(c: dict) -> str:
    m = re.search(r"D\d+", c.get("domain") or "")
    return m.group(0) if m else "—"


def opt(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        return argv[i + 1] if i + 1 < len(argv) else ""
    return None


def main(argv: list[str]) -> int:
    if DATA is None:
        print("Не найден casos.json (ни в плагине, ни в репозитории).", file=sys.stderr)
        return 1
    canon_dir = DATA / "canon" if (DATA / "canon").exists() else DATA

    if "--canon-dir" in argv:
        print(canon_dir)
        return 0
    dom_arg = opt(argv, "--canon")
    if dom_arg is not None:
        dom = re.sub(r"[^D0-9]", "", dom_arg.upper())
        num = dom.replace("D", "").zfill(2)
        files = sorted(p.name for p in canon_dir.iterdir()) if canon_dir.exists() else []
        f = next((x for x in files if x.startswith(num + "-")), None)
        print(canon_dir / f if f else f"(не найден файл канона для {dom} в {canon_dir})")
        return 0 if f else 1

    casos = json.loads((DATA / "casos.json").read_text(encoding="utf-8"))

    show = opt(argv, "--show")
    if show is not None:
        nums = [int(s) for s in re.split(r"\s*,\s*", show) if s.strip().isdigit()]
        for n in nums:
            if not 1 <= n <= len(casos):
                print(f"\n[Случая #{n} нет — диапазон 1..{len(casos)}]")
                continue
            c = casos[n - 1]
            print(f"\n===== Случай #{n} — {c.get('title')} =====")
            print(f"Эфир {c.get('dir')} · {c.get('date')} · занятие {c.get('id')} · домен: {c.get('domain')} · "
                  f"уровень {c.get('tier')} · сфера {c.get('sphere')}\n")
            print((c.get("body") or "").strip())
        print()
        return 0

    doms = None
    v = opt(argv, "--dom")
    if v is not None:
        doms = [s.strip().upper() for s in v.split(",") if s.strip()]
    v = opt(argv, "--n")
    top_n = max(1, int(v)) if v and v.isdigit() else 6
    v = opt(argv, "--tier")
    tier_filter = v.upper() if v is not None else None
    v = opt(argv, "--sphere")
    sphere_filter = v.lower() if v is not None else None

    words: list[str] = []
    for i, a in enumerate(argv):
        if a.startswith("--") or (i > 0 and argv[i - 1] in FLAGS):
            continue
        words.extend(norm(a).split())
    terms = [stem_ru(re.sub(r"[\W_]", "", w)) for w in words]
    terms = [t for t in terms if len(t) >= 3]
    if not terms:
        print('Нет слов для поиска. Пример: python3 ... buscar.py "руководитель вина опоздание" --dom D3,D8', file=sys.stderr)
        return 1

    scored = []
    for i, c in enumerate(casos):
        if doms and dom_of(c) not in doms:
            continue
        if tier_filter and (c.get("tier") or "") != tier_filter:
            continue
        if sphere_filter and (c.get("sphere") or "") != sphere_filter:
            continue
        title = norm(c.get("title") or "")
        body = norm(c.get("body") or "")
        score = 0.0
        for t in terms:
            score += title.count(t) * 3 + min(body.count(t), 3)  # потолок для тела — не награждать повторы
        weight = c.get("peso") if c.get("peso") is not None else TIER_BOOST.get(c.get("tier"), 1)
        score = round(score * weight, 2)
        if score > 0:
            scored.append((i + 1, c, score))
    scored.sort(key=lambda x: -x[2])
    scored = scored[:top_n]

    dom_txt = " в " + "/".join(doms) if doms else ""
    if not scored:
        print(f"Совпадений нет для [{', '.join(terms)}]{dom_txt}.")
        print("Попробуйте более общие слова или уберите фильтр --dom.")
        return 0
    print(f"Топ-{len(scored)} случаев для [{', '.join(terms)}]{' · домены ' + '/'.join(doms) if doms else ''}:\n")
    for n, c, score in scored:
        snip = re.sub(r"\s+", " ", c.get("body") or "")[:180]
        print(f"#{n}  [{dom_of(c)}]  [tier {c.get('tier') or '?'} · {c.get('sphere') or '?'}]  {c.get('title')}  (score {score:g})")
        print(f"      {snip}…\n")
    print("Прочитать случай целиком:  python3 <путь>/buscar.py --show <N>")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
