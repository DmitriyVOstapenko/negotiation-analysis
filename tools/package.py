#!/usr/bin/env python3
"""Сборка пакета плагина: git archive HEAD (без мастерской перевода) + паспорт сборки.

    python3 tools/package.py --out negotiation-analysis.plugin

Перед сборкой: рабочее дерево закоммичено, тесты и secret_scan --all проходят.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXCLUDE = ["translation-negociar-ru"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--allow-dirty", action="store_true", help="Собрать, даже если есть незакоммиченные изменения (в пакет они не попадут)")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    if dirty and not args.allow_dirty:
        print("Есть незакоммиченные изменения — пакет собирается из HEAD и их не увидит. Закоммитьте или --allow-dirty.", file=sys.stderr)
        return 2
    scan = subprocess.run([sys.executable, str(REPO / "tools" / "secret_scan.py"), "--all"], cwd=REPO)
    if scan.returncode != 0:
        return 1
    if out.exists():
        out.unlink()
    pathspec = ["."] + [f":(exclude){item}" for item in EXCLUDE]
    subprocess.run(["git", "archive", "--format=zip", "-o", str(out), "HEAD", "--", *pathspec], cwd=REPO, check=True)
    subprocess.run([sys.executable, str(REPO / "tools" / "build_manifest.py"), "--from-zip", str(out), "--append"], cwd=REPO, check=True)
    print(f"Пакет: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
