"""Профиль разбора встречи: full (11 артефактов) или light (только обязательства и риски).

Профиль — решение пользователя по конкретной встрече, поэтому хранится в
review.yaml рядом со шлюзом и картой спикеров:

    analysis_profile:
      mode: light            # full | light; нет ключа — full
      source: user_confirmation
      recorded_at: "…"

Допускается и короткая форма `analysis_profile: light` (ручная правка в проекте
claude.ai). Профиль меняет только состав обязательных артефактов; шлюз, карта
спикеров и проверка цитат одинаковы для обоих профилей.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import ANALYSIS_FILES, NaError, meeting_dir, now_iso, read_yaml, write_yaml


DEFAULT_PROFILE = "full"
# 07 и 08 ссылаются только на участников, организации и темы из 01 (who, to_whom,
# involved_people, involved_companies, mitigation.owner, linked_topic); на 09–11 они
# не опираются — производные 09–11 строятся из 01–08, а не наоборот. Поэтому light = 01, 07, 08.
PROFILE_FILES: dict[str, list[str]] = {
    "full": list(ANALYSIS_FILES),
    "light": ["01_extraction.json", "07_commitments.json", "08_risks.json"],
}
PROFILE_LABELS = {"full": "полный разбор", "light": "лёгкий разбор (обязательства и риски)"}
ARTIFACT_LABELS = {
    "01_extraction.json": "участники, темы, ключевые факты",
    "02_tactics.json": "тактики влияния",
    "03_manipulations.json": "манипуляции",
    "04_arguments.json": "аргументация",
    "05_power.json": "динамика участия и власти",
    "06_emotions.json": "эмоциональная динамика",
    "07_commitments.json": "обязательства и открытые вопросы",
    "08_risks.json": "риски",
    "09_decision_authority.json": "карта решений и полномочий",
    "10_argument_map.json": "карта аргументов",
    "11_meeting_effectiveness.json": "оценка эффективности",
}
REVIEW_HEADER = "review.yaml — решения пользователя: шлюз согласия, карта спикеров, границы анализа."


def profile_record(meeting_path: Path) -> dict[str, Any]:
    """Сырое значение профиля из review.yaml: {"mode", "source", "recorded_at", "declared"}."""
    try:
        review = read_yaml(meeting_path / "review.yaml")
    except NaError:
        review = {}
    raw = review.get("analysis_profile")
    if isinstance(raw, dict):
        mode = raw.get("mode")
        return {"mode": mode, "source": raw.get("source"), "recorded_at": raw.get("recorded_at"), "declared": mode is not None}
    if isinstance(raw, str):
        return {"mode": raw.strip(), "source": None, "recorded_at": None, "declared": True}
    return {"mode": None, "source": None, "recorded_at": None, "declared": False}


def meeting_profile(meeting_path: Path) -> str:
    """full | light; неизвестное или отсутствующее значение читается как full (строже)."""
    mode = profile_record(meeting_path)["mode"]
    return mode if mode in PROFILE_FILES else DEFAULT_PROFILE


def profile_error(meeting_path: Path) -> str | None:
    """Сообщение об ошибке, если в review.yaml записан неизвестный профиль."""
    record = profile_record(meeting_path)
    if record["declared"] and record["mode"] not in PROFILE_FILES:
        return f"неизвестный профиль разбора analysis_profile={record['mode']!r} в review.yaml; допустимо: {', '.join(PROFILE_FILES)}"
    return None


def required_analysis_files(meeting_path: Path) -> list[str]:
    return list(PROFILE_FILES[meeting_profile(meeting_path)])


def skipped_analysis_files(meeting_path: Path) -> list[str]:
    required = set(required_analysis_files(meeting_path))
    return [name for name in ANALYSIS_FILES if name not in required]


def profile_summary(meeting_path: Path) -> dict[str, Any]:
    profile = meeting_profile(meeting_path)
    analysis = meeting_path / "analysis"
    skipped = skipped_analysis_files(meeting_path)
    return {
        "profile": profile,
        "label": PROFILE_LABELS[profile],
        "required": required_analysis_files(meeting_path),
        "skipped_by_profile": skipped,
        "present_outside_profile": [name for name in skipped if (analysis / name).is_file()],
    }


def set_profile(root: Path, meeting_id: str, profile: str) -> dict[str, Any]:
    if profile not in PROFILE_FILES:
        raise NaError(f"--profile: допустимо {', '.join(PROFILE_FILES)}")
    path = meeting_dir(root, meeting_id)
    review_path = path / "review.yaml"
    if not review_path.is_file():
        raise NaError(f"review.yaml не найден в negotiations/{meeting_id}: сначала na.py source create")
    previous = meeting_profile(path)
    review = read_yaml(review_path)
    review["analysis_profile"] = {"mode": profile, "source": "user_confirmation", "recorded_at": now_iso()}
    write_yaml(review_path, review, header=REVIEW_HEADER)
    summary = profile_summary(path)
    analysis = path / "analysis"
    return {
        "valid": True,
        "meeting_id": meeting_id,
        "analysis_profile": profile,
        "previous_profile": previous,
        "source": "user_confirmation",
        "required": summary["required"],
        "skipped_by_profile": summary["skipped_by_profile"],
        "missing_required": [name for name in summary["required"] if not (analysis / name).is_file()],
    }
