#!/usr/bin/env python3
"""Паспорт сборки: отпечатки файлов пакета плагина → BUILD-MANIFEST.json.

Использование (из корня репозитория):
    python3 tools/build_manifest.py --out BUILD-MANIFEST.json [--from-zip пакет.plugin]

Пакет собирается `git archive`, затем паспорт дописывается в архив (tools/package.py делает оба шага).
`na.py doctor` сверяет установленные файлы с паспортом без сети. У SKILL.md сверяется тело без
YAML-шапки: при синхронизации плагинов claude.ai шапку переписывают.

Идея паспорта — из плагина ivan (github.com/Coman-os/ivan, MIT, © 2026 Maxim Opilkin); код свой.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from na_engine.doctor import MANIFEST_FILE, _body_after_frontmatter  # noqa: E402

import hashlib  # noqa: E402


def fingerprint(name: str, data: bytes) -> dict[str, str]:
    result = {"sha256": hashlib.sha256(data).hexdigest()}
    if name.endswith("SKILL.md"):
        result["body_sha256"] = hashlib.sha256(_body_after_frontmatter(data)).hexdigest()
    return result


def manifest_from_zip(path: Path) -> dict:
    files = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename == MANIFEST_FILE:
                continue
            files[info.filename] = fingerprint(info.filename, archive.read(info))
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return {
        "name": plugin.get("name"),
        "version": plugin.get("version"),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Отпечатки файлов пакета; na.py doctor сверяет установку с ними без сети.",
        "files": dict(sorted(files.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-zip", required=True, help="Собранный пакет .plugin (zip)")
    parser.add_argument("--append", action="store_true", help="Дописать паспорт в тот же архив")
    parser.add_argument("--out", help="Куда ещё записать паспорт (json)")
    args = parser.parse_args()
    package = Path(args.from_zip)
    manifest = manifest_from_zip(package)
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    if args.append:
        with zipfile.ZipFile(package, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            if MANIFEST_FILE in archive.namelist():
                print("Паспорт уже есть в архиве — соберите пакет заново", file=sys.stderr)
                return 2
            archive.writestr(MANIFEST_FILE, text)
    print(f"Паспорт {manifest['version']}: {len(manifest['files'])} файлов.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
