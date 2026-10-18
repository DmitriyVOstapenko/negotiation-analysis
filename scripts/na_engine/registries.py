"""Чтение канонических реестров knowledge/ и их сопоставление с артефактами встречи."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import read_json_or_none, read_jsonl


def registry(root: Path, relative_path: str) -> list[dict[str, Any]]:
    return read_jsonl(root / relative_path)


def all_commitments(root: Path) -> list[dict[str, Any]]:
    return registry(root, "knowledge/commitments/open.jsonl") + registry(root, "knowledge/commitments/closed.jsonl")


def all_risks(root: Path) -> list[dict[str, Any]]:
    return registry(root, "knowledge/risks/active.jsonl") + registry(root, "knowledge/risks/closed.jsonl")


def meeting_commitments(root: Path, meeting_path: Path, include_analysis: bool = True) -> list[dict[str, Any]]:
    """Обязательства встречи: записи реестра плюс (опционально) пункты 07_commitments.json, ещё не внесённые в базу."""
    meeting_id = meeting_path.name
    rows = [dict(item, record_origin="knowledge") for item in all_commitments(root) if item.get("meeting_id") == meeting_id]
    if include_analysis:
        known = {item.get("id") for item in rows}
        analysis = read_json_or_none(meeting_path / "analysis" / "07_commitments.json") or {}
        for item in analysis.get("commitments", []):
            if isinstance(item, dict) and item.get("id") not in known:
                rows.append(dict(item, record_origin="analysis"))
    return rows


def meeting_risks(root: Path, meeting_path: Path, include_analysis: bool = True) -> list[dict[str, Any]]:
    meeting_id = meeting_path.name
    rows = [
        dict(item, record_origin="knowledge")
        for item in all_risks(root)
        if item.get("first_seen_meeting_id") == meeting_id or item.get("last_seen_meeting_id") == meeting_id
    ]
    if include_analysis:
        known = {item.get("id") for item in rows}
        analysis = read_json_or_none(meeting_path / "analysis" / "08_risks.json") or {}
        for item in analysis.get("risks", []):
            if isinstance(item, dict) and item.get("id") not in known:
                rows.append(dict(item, record_origin="analysis"))
    return rows


def people_names(root: Path) -> dict[str, str]:
    """person_id → отображаемое имя по заголовкам досье knowledge/people/*.md."""
    names: dict[str, str] = {}
    people_dir = root / "knowledge" / "people"
    if people_dir.is_dir():
        for path in sorted(people_dir.glob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            heading = None
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("# "):
                    heading = line[2:].strip()
                    break
            names[path.stem] = heading or path.stem
    return names


def organization_dirs(root: Path) -> list[Path]:
    """organizations/ — основной каталог; companies/ — устаревшее имя, поддерживается на чтение."""
    result = []
    for name in ("organizations", "companies"):
        path = root / "knowledge" / name
        if path.is_dir():
            result.append(path)
    return result
