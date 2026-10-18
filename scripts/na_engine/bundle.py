"""Упаковка артефактов встречи в один JSON (для хранения как документа проекта) и распаковка."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import NaError, meeting_dir, now_iso, read_json, relative, sha256_file, write_json
from .validate import contract_version


BUNDLE_FILE = "analysis_bundle.json"
BUNDLE_VERSION = "1.0.0"
EXTRA_FILES = ("source_packet.json", "run_manifest.json")


def _bundled_paths(meeting_path: Path) -> list[Path]:
    analysis = meeting_path / "analysis"
    paths = sorted(analysis.glob("*.json")) if analysis.is_dir() else []
    paths += [meeting_path / name for name in EXTRA_FILES if (meeting_path / name).is_file()]
    return paths


def pack(root: Path, meeting_id: str) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    files: dict[str, Any] = {}
    checksums: dict[str, str] = {}
    for item in _bundled_paths(path):
        key = item.relative_to(path).as_posix()
        if key == BUNDLE_FILE:
            continue
        files[key] = read_json(item)
        checksums[key] = sha256_file(item) or ""
    if not files:
        raise NaError("Нет JSON-артефактов для упаковки: analysis/ пуст и source_packet.json отсутствует")
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "contract_version": contract_version(),
        "meeting_id": meeting_id,
        "packed_at": now_iso(),
        "files": files,
        "checksums": checksums,
    }
    write_json(path / BUNDLE_FILE, bundle)
    return {"valid": True, "meeting_id": meeting_id, "bundle": relative(path / BUNDLE_FILE, root), "files": sorted(files)}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def unpack(root: Path, meeting_id: str, force: bool = False) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    bundle_path = path / BUNDLE_FILE
    if not bundle_path.is_file():
        raise NaError(f"Нет {BUNDLE_FILE} в {relative(path, root)}")
    bundle = read_json(bundle_path)
    if bundle.get("meeting_id") != meeting_id:
        raise NaError(f"{BUNDLE_FILE}: meeting_id {bundle.get('meeting_id')!r} не совпадает с {meeting_id!r}")
    files = bundle.get("files")
    if not isinstance(files, dict):
        raise NaError(f"{BUNDLE_FILE}: поле files должно быть объектом")
    packed_at = _parse_iso(bundle.get("packed_at"))
    written: list[str] = []
    unchanged: list[str] = []
    skipped: list[dict[str, str]] = []
    for key, content in files.items():
        normalized = key.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise NaError(f"{BUNDLE_FILE}: недопустимый путь {key!r}")
        target = path / normalized
        if target.is_file():
            try:
                existing = read_json(target)
            except NaError:
                existing = None
            if existing == content:
                unchanged.append(normalized)
                continue
            modified = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
            if not force and packed_at is not None and modified > packed_at:
                skipped.append({"path": normalized, "reason": "существующий файл новее пакета; используйте --force"})
                continue
            if not force and packed_at is None:
                skipped.append({"path": normalized, "reason": "существующий файл отличается, а время упаковки неизвестно; используйте --force"})
                continue
        write_json(target, content)
        written.append(normalized)
    return {"valid": not skipped, "meeting_id": meeting_id, "written": written, "unchanged": unchanged, "skipped": skipped}
