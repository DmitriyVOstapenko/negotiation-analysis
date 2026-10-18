"""Опциональный акустический слой: инструментальные метрики сегментов без смысловых выводов.

Зависимости numpy, soundfile и librosa не входят в стандартную библиотеку и
не обязательны для остальных команд. Модуль импортируется без них и честно
сообщает, чего не хватает. Метрики — не детектор эмоций, лжи или состояния
здоровья; границы спикеров берутся из диаризации источника и могут содержать
ошибки.
"""

from __future__ import annotations

import importlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import NaError, meeting_dir, now_iso, relative, write_json


REQUIRED_MODULES = ("numpy", "soundfile", "librosa")
LIMITATIONS = [
    "Activation proxy не является детектором эмоций, стресса, лжи или состояния здоровья.",
    "Границы спикеров взяты из диаризации источника и могут содержать ошибки.",
    "Оценка высоты тона искажается перекрёстной речью, шумом помещения и короткими сегментами.",
    "Скорость речи рассчитана по тексту транскрипта, выровненному на длительность аудиосегмента.",
    "Метрики не изменяют текстовый анализ сами по себе; интерпретация требует человека.",
]


def missing_dependencies() -> list[str]:
    missing: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    return missing


def _finite(values: Any) -> list[float]:
    return [float(v) for v in values if v is not None and math.isfinite(float(v))]


def _median(values: Any) -> float | None:
    values = _finite(values)
    return statistics.median(values) if values else None


def _zscore(value: float | None, values: Any) -> float:
    values = _finite(values)
    if value is None or len(values) < 3:
        return 0.0
    center = statistics.median(values)
    mad = statistics.median(abs(v - center) for v in values)
    if mad < 1e-9:
        return 0.0
    return float((value - center) / (1.4826 * mad))


def _timestamp(ms: float) -> str:
    sec = int(ms // 1000)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _segment_features(np: Any, librosa: Any, y: Any, sr: int, seg: dict[str, Any]) -> dict[str, Any] | None:
    start = max(0, int(seg["start_ms"] * sr / 1000))
    end = min(len(y), int(seg["end_ms"] * sr / 1000))
    clip = y[start:end]
    duration = max((end - start) / sr, 1e-6)
    if len(clip) < int(0.15 * sr):
        return None
    rms = librosa.feature.rms(y=clip, frame_length=1024, hop_length=256)[0]
    rms_db = librosa.amplitude_to_db(np.maximum(rms, 1e-8), ref=1.0)
    active_threshold = max(-48.0, float(np.percentile(rms_db, 30)) - 3.0)
    active = rms_db > active_threshold
    active_fraction = float(np.mean(active)) if len(active) else 0.0
    pitch_median = pitch_iqr = None
    if duration >= 0.6 and np.max(np.abs(clip)) > 1e-4:
        try:
            f0 = librosa.yin(clip, fmin=65, fmax=400, sr=sr, frame_length=2048, hop_length=512)
            f0 = f0[np.isfinite(f0)]
            if len(f0):
                pitch_median = float(np.median(f0))
                pitch_iqr = float(np.percentile(f0, 75) - np.percentile(f0, 25))
        except Exception:  # noqa: BLE001 — оценка тона не обязательна
            pass
    words = len([w for w in str(seg.get("text", "")).replace("…", " ").split() if w.strip()])
    return {
        "start_ms": seg["start_ms"],
        "end_ms": seg["end_ms"],
        "timestamp": _timestamp(seg["start_ms"]),
        "speaker": seg.get("speaker"),
        "text": seg.get("text"),
        "duration_sec": round(duration, 3),
        "words": words,
        "words_per_sec": round(words / duration, 3),
        "rms_db_median": round(float(np.median(rms_db)), 3),
        "rms_db_p90": round(float(np.percentile(rms_db, 90)), 3),
        "intensity_range_db": round(float(np.percentile(rms_db, 90) - np.percentile(rms_db, 10)), 3),
        "pause_fraction": round(1.0 - active_fraction, 3),
        "pitch_hz_median": round(pitch_median, 3) if pitch_median else None,
        "pitch_iqr_hz": round(pitch_iqr, 3) if pitch_iqr else None,
    }


def analyze(wav_path: Path, segments_path: Path, meeting_id: str) -> dict[str, Any]:
    missing = missing_dependencies()
    if missing:
        raise NaError(
            "Аудиоанализ недоступен: отсутствуют модули " + ", ".join(missing) + ". Это опциональная возможность; установите их отдельно (например, pip install numpy soundfile librosa) или пропустите этап.",
        )
    np = importlib.import_module("numpy")
    sf = importlib.import_module("soundfile")
    librosa = importlib.import_module("librosa")
    if not wav_path.is_file():
        raise NaError(f"Аудиофайл не найден: {wav_path}")
    if not segments_path.is_file():
        raise NaError(f"Файл сегментов не найден: {segments_path}")
    y, sr = sf.read(str(wav_path), dtype="float32")
    if y.ndim > 1:
        y = np.mean(y, axis=1)
    data = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    segments = data.get("segments") or []
    for seg in segments:
        if "start_ms" not in seg or "end_ms" not in seg:
            raise NaError("Каждый сегмент должен содержать start_ms и end_ms (миллисекунды)")

    features = [feat for feat in (_segment_features(np, librosa, y, sr, seg) for seg in segments) if feat]
    by_speaker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for feat in features:
        if feat["duration_sec"] >= 1.0:
            by_speaker[str(feat["speaker"])].append(feat)
    baselines = {}
    for speaker, rows in by_speaker.items():
        baselines[speaker] = {
            "segments": len(rows),
            "speech_duration_sec": round(sum(r["duration_sec"] for r in rows), 2),
            "words_per_sec_median": round(_median(r["words_per_sec"] for r in rows) or 0.0, 3),
            "rms_db_median": round(_median(r["rms_db_median"] for r in rows) or 0.0, 3),
            "pitch_hz_median": round(_median(r["pitch_hz_median"] for r in rows), 3) if _median(r["pitch_hz_median"] for r in rows) else None,
            "pause_fraction_median": round(_median(r["pause_fraction"] for r in rows) or 0.0, 3),
        }
    scored = []
    for speaker, rows in by_speaker.items():
        pools = {key: [r[key] for r in rows] for key in ("words_per_sec", "rms_db_median", "pitch_hz_median", "intensity_range_db", "pause_fraction")}
        for row in rows:
            components = {
                "rate_z": _zscore(row["words_per_sec"], pools["words_per_sec"]),
                "loudness_z": _zscore(row["rms_db_median"], pools["rms_db_median"]),
                "pitch_z": _zscore(row["pitch_hz_median"], pools["pitch_hz_median"]),
                "intensity_range_z": _zscore(row["intensity_range_db"], pools["intensity_range_db"]),
                "low_pause_z": -_zscore(row["pause_fraction"], pools["pause_fraction"]),
            }
            capped = [max(-3.0, min(3.0, value)) for value in components.values()]
            scored.append({**row, **{k: round(v, 3) for k, v in components.items()}, "activation_proxy": round(sum(capped) / len(capped), 3)})
    top_activation = sorted([r for r in scored if r["duration_sec"] >= 2.0], key=lambda r: r["activation_proxy"], reverse=True)[:15]

    ordered = sorted(segments, key=lambda s: (s["start_ms"], s["end_ms"]))
    overlaps = []
    for i, current in enumerate(ordered):
        for nxt in ordered[i + 1 : i + 5]:
            if nxt["start_ms"] >= current["end_ms"]:
                break
            overlap_ms = min(current["end_ms"], nxt["end_ms"]) - nxt["start_ms"]
            if overlap_ms >= 200 and current.get("speaker") != nxt.get("speaker"):
                overlaps.append({"timestamp": _timestamp(nxt["start_ms"]), "overlap_ms": int(overlap_ms), "first_speaker": current.get("speaker"), "second_speaker": nxt.get("speaker")})
    gaps = []
    max_end = 0
    for seg in ordered:
        gap = seg["start_ms"] - max_end
        if gap >= 1500:
            gaps.append({"timestamp": _timestamp(max_end), "gap_ms": int(gap)})
        max_end = max(max_end, seg["end_ms"])
    target_metrics = []
    for target in data.get("targets", []) or []:
        candidates = [r for r in scored if abs(r["start_ms"] - target["start_ms"]) <= 5000]
        if candidates:
            row = min(candidates, key=lambda r: abs(r["start_ms"] - target["start_ms"]))
            target_metrics.append({"label": target.get("label"), **row})
    return {
        "meeting_id": meeting_id,
        "generated_at": now_iso(),
        "audio_file": wav_path.name,
        "sample_rate_hz": int(sr),
        "channels": 1,
        "duration_sec": round(len(y) / sr, 3),
        "method": "text-aligned acoustic features; robust within-speaker baselines; no emotion classifier",
        "speaker_baselines": baselines,
        "top_activation_segments": top_activation,
        "target_metrics": target_metrics,
        "overlaps": overlaps,
        "silence_gaps_over_1_5s": gaps,
        "limitations": LIMITATIONS,
    }


def run_audio(root: Path, meeting_id: str, wav: Path, segments: Path) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    result = analyze(Path(wav).expanduser(), Path(segments).expanduser(), meeting_id)
    output = path / "analysis" / "audio_acoustic_metrics.json"
    write_json(output, result)
    return {"valid": True, "meeting_id": meeting_id, "output": relative(output, root), "segments_scored": len(result["top_activation_segments"]), "limitations": LIMITATIONS}


def dependency_report() -> dict[str, Any]:
    missing = missing_dependencies()
    return {"valid": not missing, "available": not missing, "required": list(REQUIRED_MODULES), "missing": missing, "note": "Аудиослой опционален; остальные команды работают без него."}
