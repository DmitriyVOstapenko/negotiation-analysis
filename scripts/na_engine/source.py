"""Приём источника: SourcePacket, скелеты meta/review, шлюз и карта спикеров."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import (
    MASKING_MODES,
    PROVIDERS,
    ROUTES,
    SENSITIVITY_LEVELS,
    NaError,
    ensure_workspace,
    gate_state,
    meeting_dir,
    meeting_dirs,
    now_iso,
    read_json_or_none,
    read_text,
    read_yaml,
    relative,
    sha256_file,
    slug,
    write_json,
    write_yaml,
)


SOURCE_PACKET_VERSION = "1.0.0"

# [00:12:05] Speaker 1: текст | 00:12:05 Имя Фамилия: текст | Speaker 2: текст | [12:05] текст
_SEGMENT_RE = re.compile(
    r"^\s*(?:\[(?P<ts1>\d{1,2}:\d{2}(?::\d{2})?)\]|\((?P<ts2>\d{1,2}:\d{2}(?::\d{2})?)\)|(?P<ts3>\d{1,2}:\d{2}(?::\d{2})?))?"
    r"\s*(?:(?P<speaker>[^:\[\]\(\)]{1,60}?)\s*:\s+)?(?P<text>.*?)\s*$"
)
_SPEAKER_LABEL_RE = re.compile(r"^(?:Speaker|Спикер|Говорящий|Участник)\s*\d+$", re.IGNORECASE)


def _looks_like_speaker(label: str) -> bool:
    label = label.strip()
    if not label or len(label) > 60:
        return False
    if _SPEAKER_LABEL_RE.match(label):
        return True
    if any(char in label for char in ".?!«»\""):
        return False
    # Имя, роль в скобках не допускаем в метке; допускаем «Имя Фамилия», «И. Петров», «CFO»
    return len(label.split()) <= 4


def parse_segments(text: str) -> list[dict[str, Any]]:
    """Разобрать транскрипт на сегменты с таймкодами и метками спикеров (эвристика, без изменения текста)."""
    segments: list[dict[str, Any]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        match = _SEGMENT_RE.match(raw)
        if not match:
            segments.append({"index": len(segments) + 1, "line": number, "timestamp": None, "speaker": None, "text": raw.strip()})
            continue
        timestamp = match.group("ts1") or match.group("ts2") or match.group("ts3")
        speaker = match.group("speaker")
        body = match.group("text")
        if speaker and not _looks_like_speaker(speaker):
            body = raw.strip()
            if timestamp:
                body = re.sub(r"^\s*[\[(]?" + re.escape(timestamp) + r"[\])]?\s*", "", body)
            speaker = None
        segments.append(
            {
                "index": len(segments) + 1,
                "line": number,
                "timestamp": timestamp,
                "speaker": speaker.strip() if speaker else None,
                "text": body,
            }
        )
    return segments


def speaker_labels(segments: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for segment in segments:
        if segment.get("speaker"):
            counts[segment["speaker"]] = counts.get(segment["speaker"], 0) + 1
    # Метка считается спикером, если встречается хотя бы дважды или имеет вид Speaker N
    labels = [label for label, count in counts.items() if count >= 2 or _SPEAKER_LABEL_RE.match(label)]
    return sorted(labels, key=lambda item: (not _SPEAKER_LABEL_RE.match(item), item))


def find_duplicate(root: Path, digest: str, exclude: str | None = None) -> str | None:
    for path in meeting_dirs(root):
        if path.name == exclude:
            continue
        packet = read_json_or_none(path / "source_packet.json")
        if packet and packet.get("content_sha256") == digest:
            return path.name
        meta = read_yaml(path / "meta.yaml")
        source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
        if source.get("content_sha256") == digest:
            return path.name
    return None


def _validate_iso(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise NaError(f"{name}: ожидается дата/время в формате ISO 8601, получено {value!r}") from None
    return value


def create_source(
    root: Path,
    meeting_id: str,
    transcript: Path,
    note: Path | None = None,
    provider: str = "manual",
    captured_at: str | None = None,
    title: str | None = None,
    allow_duplicate: bool = False,
    external_id: str | None = None,
) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise NaError(f"Неизвестный provider {provider!r}; допустимо: {', '.join(PROVIDERS)}")
    transcript = Path(transcript).expanduser()
    if not transcript.is_file():
        raise NaError(f"Файл транскрипта не найден: {transcript}")
    if note is not None:
        note = Path(note).expanduser()
        if not note.is_file():
            raise NaError(f"Файл заметки не найден: {note}")
    captured_at = _validate_iso(captured_at, "--captured-at")
    ensure_workspace(root)
    target = meeting_dir(root, meeting_id, must_exist=False)
    if target.exists():
        raise NaError(f"Встреча уже существует: {relative(target, root)}")

    digest = sha256_file(transcript) or ""
    duplicate = find_duplicate(root, digest)
    if duplicate and not allow_duplicate:
        raise NaError(
            f"Отказ: транскрипт с таким же content_sha256 уже зарегистрирован как встреча {duplicate!r}. "
            "Повторная регистрация создаст двойную встречу; используйте --allow-duplicate, если это сознательное решение."
        )

    text = read_text(transcript)
    segments = parse_segments(text)
    labels = speaker_labels(segments)
    ingested_at = now_iso()
    source_id = f"src-{datetime.now().strftime('%Y%m%d')}-{slug(transcript.stem)}-{digest[:8]}"
    source_type = "document" if provider == "document" else "transcript"

    target.mkdir(parents=True)
    (target / "analysis").mkdir()
    shutil.copyfile(transcript, target / "transcript.txt")
    note_name = None
    note_sha = None
    if note is not None:
        note_name = "note.md"
        shutil.copyfile(note, target / note_name)
        note_sha = sha256_file(target / note_name)

    packet = {
        "source_packet_version": SOURCE_PACKET_VERSION,
        "source_id": source_id,
        "meeting_id": meeting_id,
        "title": title,
        "provider": provider,
        "source_type": source_type,
        "external_id_or_local_path": external_id or str(transcript),
        "original_reference": None,
        "captured_at": captured_at,
        "ingested_at": ingested_at,
        "content_sha256": digest,
        "transcript_file": "transcript.txt",
        "provider_note_file": note_name,
        "provider_note_sha256": note_sha,
        "provider_summary_is_auxiliary": True,
        "has_timestamps": any(segment.get("timestamp") for segment in segments),
        "segments_count": len(segments),
        "segments": segments,
        "speaker_labels": labels,
        "speaker_map": {},
        "diarization_claimed": bool(labels),
        "sensitivity": "unknown",
        "processing_status": "registered",
        "consent": "not_set",
        "masking_mode": "not_set",
        "allowed_routes": "not_set",
        "retention_decision": "not_set",
        "gate_recorded_at": None,
        "canonical_source_id": duplicate and _packet_source_id(root, duplicate) or source_id,
        "duplicate_of": duplicate,
    }
    write_json(target / "source_packet.json", packet)

    meta = {
        "meeting": {
            "id": meeting_id,
            "title": title or meeting_id,
            "date": captured_at,
            "timezone": None,
            "duration_min": None,
            "meeting_type": None,
            "channel": None,
            "sensitivity": "unknown",
            "our_side": [],
            "counterparty_side": [],
            "purpose": None,
            "expected_topics": [],
            "outcome": None,
            "outcome_summary": None,
            "next_meeting_planned": None,
        },
        "source": {
            "source_id": source_id,
            "provider": provider,
            "source_type": source_type,
            "external_id_or_local_path": packet["external_id_or_local_path"],
            "captured_at": captured_at,
            "ingested_at": ingested_at,
            "content_sha256": digest,
            "canonical_source_id": packet["canonical_source_id"],
            "duplicate_of": duplicate,
        },
        "artifacts": {
            "source_packet": "source_packet.json",
            "transcript": "transcript.txt",
            "provider_note": note_name,
            "report": "report.md",
            "knowledge_diff": "knowledge_diff.md",
        },
    }
    write_yaml(
        target / "meta.yaml",
        meta,
        header="meta.yaml — метаданные встречи. Поля meeting.* заполняются вручную или по подтверждению пользователя.",
    )

    review = {
        "meeting_id": meeting_id,
        "review_status": "pending",
        "reviewed_at": None,
        "processing_gate": {
            "consent": "not_set",
            "masking_mode": "not_set",
            "sensitivity": "not_set",
            "allowed_routes": "not_set",
            "retention_decision": "not_set",
            "recorded_at": None,
            "source": None,
        },
        "speaker_mapping": {
            "status": "pending",
            "source": None,
            "speakers": [{"label": label, "person_name": None, "role": None, "person_id": None} for label in labels],
        },
        "source_scope": {
            "status": "pending",
            "analyzed_from": None,
            "analyzed_until": None,
            "excluded_from": None,
            "exclusion_reason": None,
            "source": None,
        },
        "transcript_quality": {"status": "pending", "known_issues": []},
        "knowledge_update": {"diff_reviewed": False, "write_authorized": False, "authorized_at": None},
        "subsequent_confirmations": [],
        "open_review_items": [],
    }
    write_yaml(
        target / "review.yaml",
        review,
        header="review.yaml — решения пользователя: шлюз согласия, карта спикеров, границы анализа.",
    )

    return {
        "valid": True,
        "meeting_id": meeting_id,
        "meeting_dir": relative(target, root),
        "source_id": source_id,
        "content_sha256": digest,
        "segments": len(segments),
        "with_timestamps": packet["has_timestamps"],
        "speaker_labels": labels,
        "duplicate_of": duplicate,
        "processing_status": "registered",
        "next_step": "na.py source gate --meeting … --consent yes|no …",
    }


def _packet_source_id(root: Path, meeting_id: str) -> str | None:
    packet = read_json_or_none(root / "negotiations" / meeting_id / "source_packet.json")
    return packet.get("source_id") if packet else None


def record_gate(
    root: Path,
    meeting_id: str,
    consent: str,
    masking: str,
    sensitivity: str,
    routes: str,
    retain_source: str | None = None,
) -> dict[str, Any]:
    if consent not in ("yes", "no"):
        raise NaError("--consent принимает только yes или no")
    if masking not in MASKING_MODES:
        raise NaError(f"--masking: допустимо {', '.join(MASKING_MODES)}")
    if sensitivity not in SENSITIVITY_LEVELS:
        raise NaError(f"--sensitivity: допустимо {', '.join(SENSITIVITY_LEVELS)}")
    if routes not in ROUTES:
        raise NaError(f"--routes: допустимо {', '.join(ROUTES)}")
    if retain_source not in (None, "yes", "no"):
        raise NaError("--retain-source принимает только yes или no")
    path = meeting_dir(root, meeting_id)
    review_path = path / "review.yaml"
    review = read_yaml(review_path)
    if not review:
        review = {"meeting_id": meeting_id, "review_status": "pending", "reviewed_at": None}
    recorded_at = now_iso()
    retention = {"yes": "retain", "no": "delete_after_validation"}.get(retain_source or "", "not_set")
    review["processing_gate"] = {
        **(review.get("processing_gate") if isinstance(review.get("processing_gate"), dict) else {}),
        "consent": consent,
        "masking_mode": masking,
        "sensitivity": sensitivity,
        "allowed_routes": routes,
        "retention_decision": retention,
        "recorded_at": recorded_at,
        "source": "user_confirmation",
    }
    write_yaml(review_path, review, header="review.yaml — решения пользователя: шлюз согласия, карта спикеров, границы анализа.")

    status = "gate_passed" if consent == "yes" else "blocked_by_gate"
    packet_path = path / "source_packet.json"
    packet = read_json_or_none(packet_path)
    if packet is not None:
        packet.update(
            {
                "sensitivity": sensitivity,
                "consent": consent,
                "masking_mode": masking,
                "allowed_routes": routes,
                "retention_decision": retention,
                "gate_recorded_at": recorded_at,
                "processing_status": status,
            }
        )
        write_json(packet_path, packet)

    meta_path = path / "meta.yaml"
    meta = read_yaml(meta_path)
    if isinstance(meta.get("meeting"), dict):
        meta["meeting"]["sensitivity"] = sensitivity
        write_yaml(meta_path, meta, header="meta.yaml — метаданные встречи. Поля meeting.* заполняются вручную или по подтверждению пользователя.")

    return {
        "valid": True,
        "meeting_id": meeting_id,
        "processing_status": status,
        "consent": consent,
        "masking_mode": masking,
        "sensitivity": sensitivity,
        "allowed_routes": routes,
        "retention_decision": retention,
        "note": (
            "Шлюз пройден: анализ допустим в пределах разрешённого маршрута."
            if status == "gate_passed"
            else "Без consent=yes источник остаётся blocked_by_gate: смысловой анализ и внешняя отправка не допускаются."
        ),
    }


def parse_speaker_map(raw: str) -> list[dict[str, Any]]:
    """«Speaker 1=Иван Петров (CFO);Speaker 2=Анна Сидорова» → список записей."""
    entries: list[dict[str, Any]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise NaError(f"Элемент карты спикеров без «=»: {chunk!r}")
        label, value = chunk.split("=", 1)
        label = label.strip()
        value = value.strip()
        if not label or not value:
            raise NaError(f"Пустая метка или имя в элементе: {chunk!r}")
        role = None
        match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", value)
        if match:
            value, role = match.group(1).strip(), match.group(2).strip() or None
        entries.append({"label": label, "person_name": value, "role": role, "person_id": None})
    if not entries:
        raise NaError("Карта спикеров пуста")
    return entries


def set_speakers(root: Path, meeting_id: str, raw_map: str) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    entries = parse_speaker_map(raw_map)
    review_path = path / "review.yaml"
    review = read_yaml(review_path)
    existing = review.get("speaker_mapping") if isinstance(review.get("speaker_mapping"), dict) else {}
    known = {item.get("label"): item for item in existing.get("speakers", []) if isinstance(item, dict)}
    for entry in entries:
        previous = known.get(entry["label"]) or {}
        entry["person_id"] = previous.get("person_id")
        known[entry["label"]] = entry
    review["speaker_mapping"] = {
        "status": "confirmed",
        "source": "user_confirmation",
        "recorded_at": now_iso(),
        "speakers": list(known.values()),
    }
    write_yaml(review_path, review, header="review.yaml — решения пользователя: шлюз согласия, карта спикеров, границы анализа.")

    packet_path = path / "source_packet.json"
    packet = read_json_or_none(packet_path)
    unknown_labels: list[str] = []
    if packet is not None:
        packet["speaker_map"] = {item["label"]: item["person_name"] for item in known.values() if item.get("person_name")}
        unknown_labels = [label for label in packet.get("speaker_labels", []) if label not in packet["speaker_map"]]
        write_json(packet_path, packet)
    return {
        "valid": True,
        "meeting_id": meeting_id,
        "speakers": list(known.values()),
        "labels_without_mapping": unknown_labels,
        "source": "user_confirmation",
    }


def confirm_review(root: Path, meeting_id: str, status: str) -> dict[str, Any]:
    if status not in ("confirmed", "confirmed_with_open_items", "pending"):
        raise NaError("--status: допустимо confirmed, confirmed_with_open_items или pending")
    path = meeting_dir(root, meeting_id)
    review_path = path / "review.yaml"
    review = read_yaml(review_path)
    review["review_status"] = status
    review["reviewed_at"] = now_iso() if status != "pending" else None
    write_yaml(review_path, review, header="review.yaml — решения пользователя: шлюз согласия, карта спикеров, границы анализа.")
    return {"valid": True, "meeting_id": meeting_id, "review_status": status, "gate": gate_state(path)["consent"]}
