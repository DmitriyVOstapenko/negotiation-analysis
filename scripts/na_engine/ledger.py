"""Append-only журнал снимков обязательств и рисков, его проекция и сверка с реестрами."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .common import (
    EVENT_SCHEMA_PATH,
    NaError,
    append_jsonl,
    now_iso,
    read_json,
    read_jsonl,
    record_hash,
    sha256_file,
    write_json,
    write_jsonl,
)
from .schema_runtime import validate_instance


LEDGER_RELATIVE = "knowledge/events/ledger.jsonl"
SOURCES = [
    ("commitment", "open", "knowledge/commitments/open.jsonl"),
    ("commitment", "closed", "knowledge/commitments/closed.jsonl"),
    ("risk", "active", "knowledge/risks/active.jsonl"),
    ("risk", "closed", "knowledge/risks/closed.jsonl"),
]
PROJECTION_RELATIVE = "system/event_projection"
PAIRS = [
    ("knowledge/commitments/open.jsonl", f"{PROJECTION_RELATIVE}/commitments/open.jsonl"),
    ("knowledge/commitments/closed.jsonl", f"{PROJECTION_RELATIVE}/commitments/closed.jsonl"),
    ("knowledge/risks/active.jsonl", f"{PROJECTION_RELATIVE}/risks/active.jsonl"),
    ("knowledge/risks/closed.jsonl", f"{PROJECTION_RELATIVE}/risks/closed.jsonl"),
]


def event_schema() -> dict[str, Any]:
    if not EVENT_SCHEMA_PATH.is_file():
        raise NaError(f"Схема событий не найдена: {EVENT_SCHEMA_PATH}")
    return read_json(EVENT_SCHEMA_PATH)


def event_id(entity_type: str, entity_id: str, target_view: str, digest: str) -> str:
    raw = f"{entity_type}\x1f{entity_id}\x1f{target_view}\x1f{digest}"
    return "evt-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def existing_ids(root: Path) -> set[str]:
    return {event["event_id"] for event in read_jsonl(root / LEDGER_RELATIVE) if event.get("event_id")}


def sync_ledger(root: Path) -> dict[str, Any]:
    """Дописать события для записей реестров, у которых ещё нет снимка с таким же хешем (идемпотентно)."""
    schema = event_schema()
    known = existing_ids(root)
    new_events: list[dict[str, Any]] = []
    recorded_at = now_iso()
    for entity_type, target_view, relative_path in SOURCES:
        path = root / relative_path
        if not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            entity = record.get("id")
            if not entity:
                raise NaError(f"Запись без id: {relative_path}:{line_number}")
            digest = record_hash(record)
            identity = event_id(entity_type, entity, target_view, digest)
            if identity in known:
                continue
            event = {
                "event_id": identity,
                "event_version": "1.0.0",
                "recorded_at": recorded_at,
                "entity_type": entity_type,
                "entity_id": entity,
                "event_type": "canonical_snapshot_recorded",
                "target_view": target_view,
                "record_sha256": digest,
                "source": {"source_type": "canonical_jsonl_snapshot", "path": relative_path, "line": line_number},
                "payload": record,
            }
            errors = validate_instance(event, schema, schema)
            if errors:
                raise NaError(f"Невалидное событие {identity}: {errors}")
            new_events.append(event)
            known.add(identity)
    append_jsonl(root / LEDGER_RELATIVE, new_events)
    return {
        "valid": True,
        "ledger": LEDGER_RELATIVE,
        "events_appended": len(new_events),
        "events_total": len(known),
        "event_ids": [event["event_id"] for event in new_events],
        "note": "Журнал фиксирует состояния с момента первого запуска; история до него не реконструируется.",
    }


def project_ledger(root: Path) -> dict[str, Any]:
    ledger_path = root / LEDGER_RELATIVE
    if not ledger_path.is_file():
        raise NaError("Журнал отсутствует. Сначала выполните синхронизацию (refresh-all)")
    schema = event_schema()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    event_count = 0
    for line_number, line in enumerate(ledger_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        event = json.loads(line)
        errors = validate_instance(event, schema, schema)
        if errors:
            raise NaError(f"Невалидное событие ledger:{line_number}: {errors}")
        if event["record_sha256"] != record_hash(event["payload"]):
            raise NaError(f"Нарушен hash payload в ledger:{line_number}")
        latest[(event["entity_type"], event["entity_id"])] = event
        event_count += 1

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {("commitment", "open"): [], ("commitment", "closed"): [], ("risk", "active"): [], ("risk", "closed"): []}
    for event in latest.values():
        key = (event["entity_type"], event["target_view"])
        if key not in buckets:
            raise NaError(f"Недопустимая пара entity_type/target_view: {key}")
        buckets[key].append(event["payload"])
    outputs = {
        ("commitment", "open"): root / PROJECTION_RELATIVE / "commitments/open.jsonl",
        ("commitment", "closed"): root / PROJECTION_RELATIVE / "commitments/closed.jsonl",
        ("risk", "active"): root / PROJECTION_RELATIVE / "risks/active.jsonl",
        ("risk", "closed"): root / PROJECTION_RELATIVE / "risks/closed.jsonl",
    }
    for key, output in outputs.items():
        write_jsonl(output, sorted(buckets[key], key=lambda item: str(item.get("id"))))
    manifest = {
        "projection_version": "1.0.0",
        "generated_at": now_iso(),
        "ledger_sha256": sha256_file(ledger_path),
        "events_read": event_count,
        "entities_projected": len(latest),
        "counts": {
            "commitments_open": len(buckets[("commitment", "open")]),
            "commitments_closed": len(buckets[("commitment", "closed")]),
            "risks_active": len(buckets[("risk", "active")]),
            "risks_closed": len(buckets[("risk", "closed")]),
        },
    }
    write_json(root / PROJECTION_RELATIVE / "projection_manifest.json", manifest)
    return {"valid": True, **manifest}


def _load_by_id(path: Path) -> dict[str, dict[str, Any]]:
    return {str(record.get("id")): record for record in read_jsonl(path)}


def validate_projection(root: Path) -> dict[str, Any]:
    differences: list[dict[str, Any]] = []
    for canonical_relative, projected_relative in PAIRS:
        canonical = _load_by_id(root / canonical_relative)
        projected = _load_by_id(root / projected_relative)
        if canonical != projected:
            differences.append({
                "canonical": canonical_relative,
                "projection": projected_relative,
                "missing_in_projection": sorted(set(canonical) - set(projected)),
                "extra_in_projection": sorted(set(projected) - set(canonical)),
                "changed": sorted(key for key in set(canonical) & set(projected) if canonical[key] != projected[key]),
            })
    result = {"valid": not differences, "pairs_checked": len(PAIRS), "differences": differences}
    write_json(root / "system" / "event_projection_validation.json", result)
    return result
