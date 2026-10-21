"""Строгая проверка цитат по транскрипту: уровень совпадения, склейки, атрибуция, таймкоды.

Уровни совпадения (от строгого к мягкому):

- exact — цитата буквально входит в transcript.txt;
- normalized — совпадает после нормализации: регистр, ё/е, кавычки и тире любых видов,
  прочая пунктуация, пробелы и переносы строк; допустимо, но помечается;
- ellipsis — цитата с «…» или «...» найдена по частям, в порядке следования, внутри одной
  реплики или соседних реплик одного говорящего; помечается.

Ошибки (блокируют ready): цитата не найдена; найдена только через стык реплик разных
говорящих (склейка); говорящий реплики по подтверждённой карте спикеров — не тот, кому
артефакт приписал цитату; заявленный таймкод дальше допуска и совпадает с началом другой
реплики. Предупреждения: дрейф таймкода, неподтверждённая карта, неизвестный говорящий,
якорь обязательства произнёс не исполнитель. Связанные реплики обязательства
(07 related_mentions) и доказательства личности участника (01 participants[].evidence)
проверяются по транскрипту и таймкоду, но не по атрибуции.

Реплика (сегмент) — строка транскрипта, разобранная source.parse_segments; строка без
таймкода и метки продолжает предыдущую реплику. Соседние реплики одного говорящего образуют
«выступление» (run): внутри него части цитаты с «…» могут переходить через границу строк.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .common import walk_dicts
from .source import parse_segments, speaker_labels


# Допуск таймкода. Таймкод транскрипта ставится на начало реплики, а заявленный таймкод
# сравнивается со всем интервалом реплики (от её начала до начала следующей), поэтому цитата из
# середины длинной реплики расхождения не даёт. Остаток — округление и копирование из плеера
# (секунды), сдвиг между выгрузками одной записи (несколько секунд). 15 с покрывают это с
# запасом и меньше типичной длины одного обмена репликами в деловом разговоре (10–20 с):
# дальше 15 с ссылка уже ведёт читателя к соседнему обмену, и это стоит проверить.
TIMESTAMP_TOLERANCE_SEC = 15
MAX_OCCURRENCES = 500

QUOTE_KEYS = ("quote", "source_quote", "related_quote", "prior_position_quote", "reaction_quote", "evidence_quote")
# Поля, которыми артефакт приписывает цитату человеку (по контрактам 1.1.0):
# used_by — 02 тактики, 03 манипуляции; speaker — 04 аргументы, 05 framing_moments и concessions
# (цитата уступки и, если не задан prior_position_speaker, прежняя позиция), 06 sentiment_points,
# 10 позиции карты аргументов; who — 07 исполнитель обязательства. actor — на будущее (в
# контрактах 1.1.0 нет).
# strict: говорящий реплики обязан совпасть с заявленным человеком.
# soft: по правилам доказательности опорой обязательства может быть реплика другой стороны,
# принявшей обещание, поэтому расхождение — предупреждение «проверьте», а не ошибка.
# Не атрибуция: target, to_whom, person_id в 09 (наблюдение о человеке, доказательство может
# быть чужой репликой), final_decision_maker, proposal_owner, owner, involved_people.
ATTRIBUTION_KEYS = {"used_by": "strict", "speaker": "strict", "actor": "strict", "who": "soft"}
# Свой говорящий у отдельной цитаты: прежнюю позицию уступки мог назвать коллега или другая
# сторона (05 concessions.prior_position_speaker). Поле задано — атрибуция этой цитаты строгая по
# нему; null или нет поля — по общему полю записи (speaker).
QUOTE_SPEAKER_KEYS = {"prior_position_quote": "prior_position_speaker"}
# Вложенные доказательства наследуют атрибуцию записи (04 source_quotes, 10 positions[].evidence).
NESTED_EVIDENCE_KEYS = ("source_quotes", "evidence")
# Вложенные цитаты, которые проверяются по транскрипту, но атрибуцию записи не наследуют:
# 07 related_mentions — предложение, принятие, вопрос или подтверждение другой стороны, прежняя
# версия обязательства; это чужие реплики, а не слова исполнителя (who).
UNATTRIBUTED_LIST_KEYS = ("related_mentions",)
ELLIPSIS_RE = re.compile(r"…|\.{3,}")
LEVEL_LABELS = {"exact": "буквально", "normalized": "после нормализации", "ellipsis": "с пропуском «…»"}


# ---------------------------------------------------------------------------
# Нормализация и таймкоды
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Регистр, ё/е, кавычки, тире, пунктуация, пробелы и переносы строк не различаются."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return re.sub(r"[\W_]+", " ", text).strip()


def to_seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    """1 цитата, 2 цитаты, 5 цитат."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} {many}"
    return f"{count} {one if count % 10 == 1 else few if 2 <= count % 10 <= 4 else many}"


def quotes_word(count: int) -> str:
    return plural_ru(count, "цитаты", "цитат", "цитат")  # «атрибуция 1 цитаты / 3 цитат не проверена»


def name_tokens(value: Any) -> set[str]:
    return set(normalize(value).split()) if isinstance(value, str) else set()


def names_match(left: Any, right: Any) -> bool:
    """«Анна Крылова» ~ «Крылова Анна Сергеевна»: множество слов одного входит в другое."""
    a, b = name_tokens(left), name_tokens(right)
    return bool(a and b) and (a <= b or b <= a)


# ---------------------------------------------------------------------------
# Индекс транскрипта
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    index: int
    line: int
    label: str | None
    ts_text: str | None
    own_ts: int | None
    eff_ts: int | None
    run: int
    line_start: int
    line_end: int
    norm_start: int = -1
    norm_end: int = -1
    norm: str = ""


class TranscriptIndex:
    """Сегменты транскрипта с позициями в исходном тексте и в нормализованном потоке реплик."""

    def __init__(self, text: str) -> None:
        self.text = text
        parsed = parse_segments(text)
        self.labels = speaker_labels(parsed)
        accepted = set(self.labels)
        raw_lines = text.splitlines()
        offsets: list[int] = []
        position = 0
        for chunk in text.splitlines(keepends=True):
            offsets.append(position)
            position += len(chunk)

        self.segments: list[Segment] = []
        pieces: list[str] = []
        stream_length = 0
        run = -1
        previous: Segment | None = None
        for item in parsed:
            number = item["line"]
            raw = raw_lines[number - 1]
            line_start = offsets[number - 1]
            label = item.get("speaker") if item.get("speaker") in accepted else None
            ts_text = item.get("timestamp")
            if label:
                body = item.get("text") or ""
            else:
                # метка не признана спикером (встречается один раз и не «Speaker N») — она часть текста
                body = raw.strip()
                if ts_text:
                    body = re.sub(r"^\s*[\[(]?" + re.escape(ts_text) + r"[\])]?\s*", "", body, count=1)
            continuation = label is None and ts_text is None and previous is not None
            if continuation:
                label = previous.label
            own = to_seconds(ts_text)
            eff = own if own is not None else (previous.eff_ts if previous else None)
            shown = ts_text if ts_text else (previous.ts_text if previous else None)
            if previous is None or label != previous.label or (label is None and not continuation):
                run += 1
            segment = Segment(len(self.segments), number, label, shown, own, eff, run, line_start, line_start + len(raw))
            norm = normalize(body)
            if norm:
                if pieces:
                    stream_length += 1
                segment.norm_start, segment.norm_end, segment.norm = stream_length, stream_length + len(norm), norm
                pieces.append(norm)
                stream_length += len(norm)
            self.segments.append(segment)
            previous = segment
        self.stream = " ".join(pieces)
        self._line_starts = [segment.line_start for segment in self.segments]
        self._norm_segments = [segment for segment in self.segments if segment.norm]
        self._norm_starts = [segment.norm_start for segment in self._norm_segments]
        self._next_own: list[int | None] = [None] * len(self.segments)
        upcoming: int | None = None
        for segment in reversed(self.segments):
            self._next_own[segment.index] = upcoming
            if segment.own_ts is not None:
                upcoming = segment.own_ts
        self.starts_by_second: dict[int, list[Segment]] = {}
        for segment in self.segments:
            if segment.own_ts is not None:
                self.starts_by_second.setdefault(segment.own_ts, []).append(segment)

    # --- сведения об источнике

    def quality(self) -> dict[str, Any]:
        timestamped = sum(1 for segment in self.segments if segment.own_ts is not None)
        return {
            "segments": len(self.segments),
            "timestamped_segments": timestamped,
            "has_timestamps": timestamped > 0,
            "has_speaker_labels": bool(self.labels),
            "speaker_labels": list(self.labels),
            "labelled_segments": sum(1 for segment in self.segments if segment.label),
        }

    # --- поиск

    def _segments_for_raw(self, start: int, end: int) -> tuple[int, int] | None:
        if not self.segments:
            return None
        first = max(bisect.bisect_right(self._line_starts, start) - 1, 0)
        last = first
        while last + 1 < len(self.segments) and self.segments[last + 1].line_start < end:
            last += 1
        return first, last

    def _segments_for_stream(self, start: int, end: int) -> tuple[int, int] | None:
        if not self._norm_segments:
            return None
        first = max(bisect.bisect_right(self._norm_starts, start) - 1, 0)
        last = first
        while last + 1 < len(self._norm_segments) and self._norm_segments[last + 1].norm_start < end:
            last += 1
        return self._norm_segments[first].index, self._norm_segments[last].index

    def _stream_occurrences(self, needle: str, start: int = 0) -> list[int]:
        found: list[int] = []
        if not needle:
            return found
        position = self.stream.find(needle, start)
        while position != -1 and len(found) < MAX_OCCURRENCES:
            end = position + len(needle)
            if (position == 0 or self.stream[position - 1] == " ") and (end == len(self.stream) or self.stream[end] == " "):
                found.append(position)
            position = self.stream.find(needle, position + 1)
        return found

    def _first_occurrence(self, needle: str, start: int) -> int | None:
        position = self.stream.find(needle, start)
        while position != -1:
            end = position + len(needle)
            if (position == 0 or self.stream[position - 1] == " ") and (end == len(self.stream) or self.stream[end] == " "):
                return position
            position = self.stream.find(needle, position + 1)
        return None

    def literal_spans(self, quote: str) -> list[tuple[int, int, int]]:
        spans = []
        position = self.text.find(quote)
        while position != -1 and len(spans) < MAX_OCCURRENCES:
            located = self._segments_for_raw(position, position + len(quote))
            if located:
                spans.append((located[0], located[1], position))
            position = self.text.find(quote, position + 1)
        return spans

    def normalized_spans(self, quote: str) -> list[tuple[int, int, int]]:
        needle = normalize(quote)
        spans = []
        for position in self._stream_occurrences(needle):
            located = self._segments_for_stream(position, position + len(needle))
            if located:
                spans.append((located[0], located[1], position))
        return spans

    def ellipsis_spans(self, quote: str) -> list[tuple[int, int, int]]:
        parts = [normalize(part) for part in ELLIPSIS_RE.split(quote)]
        parts = [part for part in parts if part]
        if not parts:
            return []
        spans = []
        for start in self._stream_occurrences(parts[0]):
            end = start + len(parts[0])
            complete = True
            for part in parts[1:]:
                found = self._first_occurrence(part, end)
                if found is None:
                    complete = False
                    break
                end = found + len(part)
            if not complete:
                break  # дальше по потоку ранние части встречаются только позже — остальные тоже не сложатся
            located = self._segments_for_stream(start, end)
            if located:
                spans.append((located[0], located[1], start))
        return spans

    def time_span(self, first: int, last: int) -> tuple[int, int] | None:
        start = self.segments[first].eff_ts
        if start is None:
            return None
        upcoming = self._next_own[last]
        last_ts = self.segments[last].eff_ts
        end = upcoming - 1 if upcoming is not None and upcoming > start else (last_ts if last_ts is not None else start)
        return start, max(end, start)


# ---------------------------------------------------------------------------
# Карта спикеров и участники
# ---------------------------------------------------------------------------


class SpeakerContext:
    def __init__(self, review: dict[str, Any], participants: list[Any]) -> None:
        mapping = review.get("speaker_mapping") if isinstance(review.get("speaker_mapping"), dict) else {}
        self.map_status = str(mapping.get("status") or "pending")
        self.confirmed = self.map_status == "confirmed"
        self.entries: dict[str, dict[str, Any]] = {}
        for entry in mapping.get("speakers") or []:
            if isinstance(entry, dict) and entry.get("label") and (entry.get("person_name") or entry.get("person_id")):
                self.entries[str(entry["label"])] = entry
        self.participants: dict[str, str] = {}
        for item in participants or []:
            if isinstance(item, dict) and item.get("person_id"):
                self.participants[str(item["person_id"])] = str(item.get("full_name") or item["person_id"])

    def _id_by_name(self, name: Any) -> str | None:
        matches = [pid for pid, full_name in self.participants.items() if names_match(name, full_name)]
        return matches[0] if len(matches) == 1 else None

    def person_for_label(self, label: str | None) -> tuple[str | None, str | None]:
        if not label:
            return None, None
        entry = self.entries.get(label)
        if entry:
            pid = entry.get("person_id") or self._id_by_name(entry.get("person_name"))
            return pid, entry.get("person_name") or self.participants.get(pid or "")
        if label in self.participants:
            return label, self.participants[label]
        pid = self._id_by_name(label)
        if pid:
            return pid, self.participants[pid]
        return None, None

    def claimed_person(self, value: str) -> tuple[str | None, str | None]:
        if value in self.participants:
            return value, self.participants[value]
        pid = self._id_by_name(value)
        return (pid, self.participants[pid]) if pid else (None, None)

    def unmapped(self, labels: list[str]) -> list[str]:
        return [label for label in labels if self.person_for_label(label) == (None, None)]


# ---------------------------------------------------------------------------
# Сбор цитат из артефактов
# ---------------------------------------------------------------------------


def _claimed_timestamps(node: dict[str, Any], key: str) -> tuple[str | None, str | None]:
    if key == "quote":
        if isinstance(node.get("timestamp"), str):
            return node["timestamp"], node["timestamp"]
        if isinstance(node.get("timestamp_start"), str):
            end = node.get("timestamp_end")
            return node["timestamp_start"], end if isinstance(end, str) else node["timestamp_start"]
        return None, None
    value = node.get(key[: -len("quote")] + "timestamp")
    return (value, value) if isinstance(value, str) else (None, None)


def collect_quotes(filename: str, data: Any) -> list[dict[str, Any]]:
    """Все непустые цитаты файла с заявленным таймкодом и полем атрибуции (если есть)."""
    attributed: dict[int, tuple[str, str, str]] = {}
    unattributed: set[int] = set()
    for node, _ in walk_dicts(data):
        for list_key in UNATTRIBUTED_LIST_KEYS:
            child = node.get(list_key)
            if isinstance(child, list):
                unattributed.update(id(element) for element in child if isinstance(element, dict))
        for key, mode in ATTRIBUTION_KEYS.items():
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                attributed[id(node)] = (key, value.strip(), mode)
                for child_key in NESTED_EVIDENCE_KEYS:
                    child = node.get(child_key)
                    if isinstance(child, list):
                        for element in child:
                            if isinstance(element, dict):
                                attributed.setdefault(id(element), (key, value.strip(), mode))
                break
    items: list[dict[str, Any]] = []
    for node, path in walk_dicts(data):
        for key in QUOTE_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value:
                start, end = _claimed_timestamps(node, key)
                attribution = None if id(node) in unattributed else attributed.get(id(node))
                speaker_key = QUOTE_SPEAKER_KEYS.get(key)
                own_speaker = node.get(speaker_key) if speaker_key else None
                if isinstance(own_speaker, str) and own_speaker.strip():
                    attribution = (speaker_key, own_speaker.strip(), "strict")
                items.append({"file": filename, "path": f"{path}.{key}", "quote": value, "claimed_start": start, "claimed_end": end, "attribution": attribution})
    return items


# ---------------------------------------------------------------------------
# Проверка
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    method: str
    first: int
    last: int
    position: int
    status: str = "ok"
    labels: list[str] = field(default_factory=list)
    span: tuple[int, int] | None = None
    delta: int | None = None


def _evaluate(index: TranscriptIndex, method: str, first: int, last: int, position: int) -> Candidate:
    covered = index.segments[first : last + 1]
    labels: list[str] = []
    for segment in covered:
        if segment.label and segment.label not in labels:
            labels.append(segment.label)
    runs = {segment.run for segment in covered}
    if len(runs) == 1:
        status = "ok"
    elif len(labels) >= 2:
        status = "splice"
    else:
        status = "unlabeled_span"
    return Candidate(method, first, last, position, status, labels, index.time_span(first, last))


def _delta(span: tuple[int, int] | None, claimed: tuple[int, int] | None) -> int | None:
    if span is None or claimed is None:
        return None
    return max(0, span[0] - claimed[1], claimed[0] - span[1])


def _where(index: TranscriptIndex, candidate: Candidate) -> str:
    segment = index.segments[candidate.first]
    return f"{segment.ts_text or 'строка ' + str(segment.line)}, {segment.label or 'говорящий не размечен'}"


def check_quotes(transcript: str, items: list[dict[str, Any]], review: dict[str, Any], participants: list[Any]) -> dict[str, Any]:
    index = TranscriptIndex(transcript)
    speakers = SpeakerContext(review, participants)
    errors: list[str] = []
    warnings: list[str] = []
    stats = {
        "checked": 0, "exact": 0, "normalized": 0, "ellipsis": 0, "not_found": 0, "spliced": 0, "unlabeled_span": 0,
        "attribution_checked": 0, "attribution_matched": 0, "attribution_mismatched": 0, "attribution_unverified": 0,
        "timestamp_checked": 0, "timestamp_drift": 0, "timestamp_other_reply": 0,
    }
    checks: list[dict[str, Any]] = []
    unknown_labels: dict[str, int] = {}
    unknown_claimed: dict[str, int] = {}
    no_label_quotes = 0
    no_time_quotes = 0

    def attribution_rank(candidate: Candidate, attribution: tuple[str, str, str] | None) -> int:
        if not attribution or len(candidate.labels) != 1:
            return 1
        pid, name = speakers.person_for_label(candidate.labels[0])
        claimed_pid, claimed_name = speakers.claimed_person(attribution[1])
        same = (pid and claimed_pid and pid == claimed_pid) or names_match(name, claimed_name or attribution[1])
        return 0 if same else 1

    for item in items:
        stats["checked"] += 1
        quote = item["quote"]
        where = f"{item['file']} {item['path']}"
        claimed_start, claimed_end = to_seconds(item["claimed_start"]), to_seconds(item["claimed_end"])
        claimed = (claimed_start, claimed_end if claimed_end is not None and claimed_end >= claimed_start else claimed_start) if claimed_start is not None else None
        attribution = item["attribution"]

        candidates = [_evaluate(index, "exact", *span) for span in index.literal_spans(quote)]
        accepted = [c for c in candidates if c.status != "splice"]
        if not accepted:
            method_spans = index.ellipsis_spans(quote) if ELLIPSIS_RE.search(quote) else index.normalized_spans(quote)
            method = "ellipsis" if ELLIPSIS_RE.search(quote) else "normalized"
            extra = [_evaluate(index, method, *span) for span in method_spans]
            candidates += extra
            accepted = [c for c in extra if c.status != "splice"]
        for candidate in candidates:
            candidate.delta = _delta(candidate.span, claimed)

        record: dict[str, Any] = {"file": item["file"], "path": item["path"], "claimed_timestamp": item["claimed_start"]}
        if not accepted:
            spliced = [c for c in candidates if c.status == "splice"]
            if spliced:
                best = min(spliced, key=lambda c: (c.delta if c.delta is not None else 0, c.position))
                stats["spliced"] += 1
                parts = []
                for segment in index.segments[best.first : best.last + 1]:
                    piece = f"{segment.label} ({segment.ts_text or 'строка ' + str(segment.line)})"
                    if segment.label and piece not in parts:
                        parts.append(piece)
                errors.append(f"склейка реплик разных говорящих: {where} — цитата складывается только через стык реплик: {', '.join(parts[:4])}")
                record.update({"level": None, "status": "spliced", "line": index.segments[best.first].line, "timestamp": index.segments[best.first].ts_text, "speaker_labels": best.labels})
            else:
                stats["not_found"] += 1
                errors.append(f"quote not found in transcript: {where}")
                record.update({"level": None, "status": "not_found"})
            checks.append(record)
            continue

        def rank(candidate: Candidate) -> tuple[int, int, int, int, int]:
            bucket = 0 if candidate.delta is None or candidate.delta <= TIMESTAMP_TOLERANCE_SEC else 1
            return (0 if candidate.status == "ok" else 1, bucket, attribution_rank(candidate, attribution), candidate.delta or 0, candidate.position)

        best = min(accepted, key=rank)
        stats[best.method] += 1
        segment = index.segments[best.first]
        record.update({
            "level": best.method,
            "status": "found",
            "line": segment.line,
            "timestamp": segment.ts_text,
            "speaker_labels": best.labels,
            "segments": best.last - best.first + 1,
        })
        if best.status == "unlabeled_span":
            stats["unlabeled_span"] += 1
            warnings.append(f"цитата охватывает реплики без разметки говорящего, склейку исключить нельзя: {where}")

        # таймкод
        if claimed is not None and best.span is not None:
            stats["timestamp_checked"] += 1
            record["timestamp_delta_sec"] = best.delta
            if best.delta is not None and best.delta > TIMESTAMP_TOLERANCE_SEC:
                own_runs = {s.run for s in index.segments[best.first : best.last + 1]}
                other = [s for s in index.starts_by_second.get(claimed[0], []) if s.run not in own_runs]
                if other:
                    stats["timestamp_other_reply"] += 1
                    target = other[0]
                    errors.append(
                        f"таймкод указывает на другую реплику: {where} — заявлен {item['claimed_start']} "
                        f"(реплика {target.label or 'без метки'}, строка {target.line}), цитата найдена в {_where(index, best)}; расхождение {best.delta} с"
                    )
                    record["timestamp_check"] = "other_reply"
                else:
                    stats["timestamp_drift"] += 1
                    warnings.append(
                        f"таймкод расходится с репликой цитаты на {best.delta} с (допуск {TIMESTAMP_TOLERANCE_SEC} с): {where} — "
                        f"заявлен {item['claimed_start']}, найдена в {_where(index, best)}"
                    )
                    record["timestamp_check"] = "drift"
            else:
                record["timestamp_check"] = "ok"
        elif claimed is not None:
            no_time_quotes += 1

        # атрибуция
        if attribution:
            key, value, mode = attribution
            stats["attribution_checked"] += 1
            result = "unverified"
            person_pid, person_name = (None, None)
            if len(best.labels) != 1:
                no_label_quotes += 1
            else:
                label = best.labels[0]
                person_pid, person_name = speakers.person_for_label(label)
                claimed_pid, claimed_name = speakers.claimed_person(value)
                if person_pid is None and person_name is None:
                    unknown_labels[label] = unknown_labels.get(label, 0) + 1
                elif (person_pid and claimed_pid and person_pid == claimed_pid) or names_match(person_name, claimed_name or value):
                    result = "match"
                elif claimed_pid is None and claimed_name is None:
                    unknown_claimed[value] = unknown_claimed.get(value, 0) + 1
                else:
                    result = "mismatch"
                    who = f"{key}={value!r}" + (f" ({claimed_name})" if claimed_name and claimed_name != value else "")
                    actual = person_name or person_pid
                    if mode == "soft":
                        warnings.append(
                            f"якорь обязательства произнёс не исполнитель: {where} — {who}, а реплика {_where(index, best)} → {actual}; "
                            "проверьте, что исполнитель сам принял обязательство или опора — реплика принявшей стороны "
                            "(условие подано исполнителем как предложение)"
                        )
                    elif speakers.confirmed:
                        errors.append(f"атрибуция цитаты не совпадает с картой спикеров: {where} — {who}, а реплика {_where(index, best)} принадлежит: {actual}")
                    else:
                        warnings.append(
                            f"атрибуция цитаты не совпадает с картой спикеров (карта не подтверждена — предупреждение): {where} — {who}, "
                            f"а реплика {_where(index, best)} → {actual}"
                        )
            stats[f"attribution_{'matched' if result == 'match' else 'mismatched' if result == 'mismatch' else 'unverified'}"] += 1
            record["attribution"] = {"key": key, "claimed": value, "mode": mode, "speaker_person": person_name, "result": result}
        checks.append(record)

    quality = index.quality()
    quality.update({"speaker_map_status": speakers.map_status, "unmapped_labels": speakers.unmapped(index.labels)})
    if no_label_quotes:
        if not quality["has_speaker_labels"]:
            warnings.append(f"в записи нет разметки говорящих: атрибуция {quotes_word(no_label_quotes)} не проверялась")
        else:
            warnings.append(f"атрибуция {quotes_word(no_label_quotes)} не проверена: реплики без метки говорящего")
    for label, count in sorted(unknown_labels.items()):
        warnings.append(f"метка «{label}» не сопоставлена человеку в карте спикеров: атрибуция {quotes_word(count)} не проверена")
    for value, count in sorted(unknown_claimed.items()):
        warnings.append(f"«{value}» нет среди участников 01_extraction.json: атрибуция {quotes_word(count)} не проверена")
    if no_time_quotes:
        reason = "у реплик-источников нет таймкода" if quality["has_timestamps"] else "в записи нет таймкодов"
        warnings.append(f"{reason}: близость таймкодов {quotes_word(no_time_quotes)} не проверялась")
    return {
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "source_quality": quality,
        "quote_checks": checks,
        "timestamp_tolerance_sec": TIMESTAMP_TOLERANCE_SEC,
    }


def transcript_quality(text: str) -> dict[str, Any]:
    """Разметка говорящих и таймкоды записи — для строки «Данные:» без запуска проверки."""
    return TranscriptIndex(text).quality()
