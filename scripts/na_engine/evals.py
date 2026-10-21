"""Оценка разбора встречи по заранее заданному несущему ядру (core.json) — без LLM.

`na.py eval --meeting <id> --core <core.json>` читает артефакты разбора (01, 05, 07, 08, 09 —
какие есть; если папки analysis/ нет — из analysis_bundle.json), сопоставляет каждый пункт
ядра с находками и ставит FULL / PARTIAL / MISSING / DISTORTED с объяснением; отдельно
показывает лишние находки, противоречащие ядру. Результат — negotiations/<id>/eval_result.json
и eval_result.md. PASS — только если все пункты уровня core FULL и среди лишних находок нет
DISTORTED. Общий процент не считается. Методика — docs/EVALS.md.

Сопоставление детерминированное:

- опорная цитата → строка транскрипта: буквально после нормализации (регистр, ё/е,
  пунктуация, пробелы), с «…» — по частям в соседних строках; запасной путь — нечёткое
  совпадение слов внутри строки (помечается как недословная цитата);
- исполнитель, ЛПР, владелец → человек ядра: по id, по имени участника из 01 (все слова имени
  из ядра входят в имя), по метке спикера, по транслитерации slug (`p-danilov-…` → Данилов);
- формулировка → ключевые слова ядра: группы основ слов, каждая группа — хотя бы одна
  альтернатива; числа сравниваются целиком, слова — по началу;
- утверждение о согласии в факте или карте решений: утвердительная форма без отрицания, без
  признаков отсрочки, без описания замены или отмены и не в пересказе чужих слов
  (`claims_decision_text`).

Модуль намеренно не зависит от validate.py и quote_check.py: чтение и нормализация — свои,
чтобы оценка не менялась вместе с правилами валидатора.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .common import NaError, meeting_dir, read_yaml, write_json, write_text


EVAL_VERSION = "0.2.0"

STATUSES = ("FULL", "PARTIAL", "MISSING", "DISTORTED")
STATUS_RANK = {"FULL": 3, "PARTIAL": 2, "DISTORTED": 1, "MISSING": 0}
LEVELS = ("core", "supporting")
KINDS = ("commitment", "risk", "concession", "agreed_term", "authority", "not_decision", "open_question", "participant", "outcome")
OUTCOMES = ("agreed", "partial", "deferred", "failed", "no_decision")
CONFIDENCE_ORDER = {"speculative": 0, "possible": 1, "probable": 2, "certain": 3}
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

ARTIFACTS = ("01_extraction.json", "05_power.json", "07_commitments.json", "08_risks.json", "09_decision_authority.json")

# Где искать находки для каждого вида пункта ядра.
SOURCES = {
    "commitment": ("commitment",),
    "risk": ("risk",),
    "concession": ("concession", "key_fact"),
    "agreed_term": ("decision_area", "key_fact", "commitment", "concession", "topic"),
    "authority": ("decision_area", "authority_observation", "unresolved"),
}
# Находка такого вида закрепляется за одним пунктом ядра того же вида (реестровые записи).
EXCLUSIVE_KINDS = {"commitment", "risk", "concession"}

TIMESTAMP_TOLERANCE_SEC = 15
FUZZY_MIN_SHARE = 0.8

# Утвердительные формы: находка с ними (без отрицания и без признаков отсрочки) заявляет, что решение есть.
_AFFIRMATIVE = {
    "договорились", "договорилась", "договорился", "договоренность", "договоренности",
    "согласован", "согласована", "согласовано", "согласованы", "согласовали", "согласовал", "согласовала",
    "принят", "принята", "принято", "приняты", "приняли", "принял", "приняла",
    "утвержден", "утверждена", "утверждено", "утверждены", "утвердили", "утвердил", "утвердила",
    "решено", "решили", "одобрен", "одобрена", "одобрено", "одобрены", "одобрили",
    "подписан", "подписана", "подписано", "подписаны", "подписали",
    "закрыт", "закрыта", "закрыто", "закрыты", "закрыли", "agreed", "approved", "accepted", "decided",
}
_NEGATIONS = {"не", "нет", "ни", "not", "no"}
_DEFERRAL_PREFIXES = (
    "отлож", "перенес", "перенос", "открыт", "будет", "будут", "предстоит", "ожида", "вариант", "предлож",
    "рассматрива", "обсужда", "отмен", "снят", "неясн", "deferred", "pending", "open", "proposed", "option",
    # будущее время: «примет решение», «согласует с директором» — решения ещё нет
    "примет", "примут", "примем", "решит", "решат", "решим", "утвердит", "утвердят", "согласует", "согласуют", "согласуем",
)

# Замена и отмена — в любом месте текста: описана переигранная или снятая договорённость.
# Только прошедшее время и краткие формы («заменена», «пересмотрели», «сняла»): настоящее время
# («заменяет исходную») и полное причастие «пересмотренный» говорят о новой, действующей версии.
_REPLACED_RE = re.compile(
    r"замен(?:ен[аоы]?|енн\w*|ил\w*|а|у|е|ой|ы)"
    r"|пересмотр(?:ен[аоы]?|ел\w*|а|е|у|ом)?"
    r"|переигр\w*|устаре\w*|снял\w*|снят\w*|отозван\w*|отозвал[аио]?|аннулир\w*|недейств\w*"
)
# Существительное рядом с утвердительной формой — предмет договорённости, а не её замена:
# «договорились о замене компрессора», «согласован пересмотр цены».
_REPLACEMENT_NOUNS = {"замена", "замену", "замене", "заменой", "замены", "пересмотр", "пересмотра", "пересмотре", "пересмотру", "пересмотром"}
_NOUN_LINKS = {"о", "об", "на"}
# «Прежняя / исходная / первоначальная …» — замена, только если сказано, что она больше не действует.
_FORMER_PREFIXES = ("прежн", "исходн", "первоначальн")
_NO_LONGER = (("не", "действ"), ("больше", "не"), ("уже", "не"))

# Пересказ чужих слов: утвердительная форма после глагола речи или просьбы и «что» (двоеточия,
# открывающей кавычки) — это чужое утверждение, а не вывод разбора: «попросил записать, что сделка
# закрыта», «заявил: «договорились»». Действует только в пределах предложения и до «но»/«однако».
_REPORT_VERB_PREFIXES = ("попрос", "просил", "просьб", "заяв", "объяв", "сказал", "утвержда", "назвал", "сообщ", "говор")
_REPORT_PHRASES = (("со", "слов"), ("по", "словам"), ("по", "заявлению"), ("по", "мнению"), ("по", "версии"))
# Опровержение снимает утверждение в любом месте того же предложения («запись «штраф согласован»
# опровергнута»), если перед ним нет «не» («не оспорил»).
_REFUTATION_PREFIXES = ("опроверг", "оспор", "отриц", "отверг")
_CLAUSE_BREAKS = {"но", "однако"}
_ABBREVIATIONS = {"т", "г", "гг", "д", "п", "с", "ч", "тыс", "руб", "млн", "млрд", "коп", "шт", "ул", "стр", "им", "др", "пр", "см", "мин", "тел"}


# ---------------------------------------------------------------------------
# Нормализация и сопоставление слов
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[0-9a-zа-я]+")


def normalize(text: Any) -> str:
    """Нижний регистр, ё→е, только буквы и цифры через один пробел."""
    value = unicodedata.normalize("NFKC", str(text or "")).lower().replace("ё", "е")
    return " ".join(_WORD_RE.findall(value))


def words(text: Any) -> list[str]:
    return normalize(text).split()


def _token_match(token: str, pattern: str) -> bool:
    if pattern.isdigit():
        return token == pattern
    return token.startswith(pattern)


def phrase_in(tokens: list[str], phrase: str) -> bool:
    """Фраза из основ слов встречается подряд (каждое слово — по началу, числа — целиком)."""
    pattern = words(phrase)
    if not pattern:
        return False
    size = len(pattern)
    for start in range(len(tokens) - size + 1):
        if all(_token_match(tokens[start + offset], pattern[offset]) for offset in range(size)):
            return True
    return False


def groups_hit(tokens: list[str], groups: list[list[str]] | None) -> bool:
    """Каждая группа представлена хотя бы одной альтернативой."""
    if not groups:
        return False
    return all(any(phrase_in(tokens, alternative) for alternative in group) for group in groups)


def missing_groups(tokens: list[str], groups: list[list[str]] | None) -> list[list[str]]:
    return [group for group in groups or [] if not any(phrase_in(tokens, alternative) for alternative in group)]


def present_alternatives(tokens: list[str], groups: list[list[str]] | None) -> list[str]:
    found = []
    for group in groups or []:
        found.extend(alternative for alternative in group if phrase_in(tokens, alternative))
    return found


def _sentences(text: Any) -> list[str]:
    """Предложения текста: границы — «!», «?», «;», перевод строки и одиночная точка не после
    инициала, сокращения («т.», «тыс.», «руб.») и не внутри числа; многоточие предложение не делит."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    parts: list[str] = []
    start = 0
    for match in re.finditer(r"\.{2,}|[.!?;\n]", value):
        mark = match.group()
        if len(mark) > 1:  # многоточие
            continue
        if mark == ".":
            if match.end() < len(value) and value[match.end()].isdigit():
                continue
            previous = re.search(r"(\w+)\W*$", value[start:match.start()])
            word = previous.group(1).lower() if previous else ""
            if not word.isdigit() and (len(word) <= 1 or word in _ABBREVIATIONS):
                continue
        parts.append(value[start:match.end()])
        start = match.end()
    parts.append(value[start:])
    return [part for part in parts if normalize(part)]


def _has_pair(tokens: list[str], pair: tuple[str, str]) -> bool:
    first, second = pair
    for index in range(len(tokens) - 1):
        following = tokens[index + 1]
        if tokens[index] == first and (following in ("не", "нет") if second == "не" else following.startswith(second)):
            return True
    return False


def _describes_replacement(tokens: list[str]) -> bool:
    """В тексте сказано, что договорённость заменена, переиграна, снята или больше не действует."""
    for index, token in enumerate(tokens):
        if not _REPLACED_RE.fullmatch(token):
            continue
        if token in _REPLACEMENT_NOUNS:
            before = tokens[index - 1] if index > 0 else ""
            linked = index > 1 and before in _NOUN_LINKS and tokens[index - 2] in _AFFIRMATIVE
            after = tokens[index + 1] if index + 1 < len(tokens) else ""
            if before in _AFFIRMATIVE or linked or after in _AFFIRMATIVE:
                continue
        return True
    if any(token.startswith(_FORMER_PREFIXES) for token in tokens):
        return any(_has_pair(tokens, pair) for pair in _NO_LONGER)
    return False


# Слова, а также двоеточие перед пояснением и открывающая кавычка: они вводят чужие слова, как «что».
_CLAUSE_TOKEN_RE = re.compile(r"[0-9a-zа-я]+|:(?=\s)|[«„]")
_INTRODUCERS = {"что", ":", "«", "„"}


def _clauses(sentence: str) -> list[list[str]]:
    value = unicodedata.normalize("NFKC", sentence).lower().replace("ё", "е")
    clauses: list[list[str]] = [[]]
    for token in _CLAUSE_TOKEN_RE.findall(value):
        if token in _CLAUSE_BREAKS:
            clauses.append([])
        else:
            clauses[-1].append(token)
    return [clause for clause in clauses if clause]


def _previous_word(clause: list[str], index: int) -> str:
    for position in range(index - 1, -1, -1):
        if clause[position] not in (":", "«", "„"):
            return clause[position]
    return ""


def _reported(clause: list[str], index: int) -> bool:
    """Утвердительная форма clause[index] — пересказ чужих слов или опровергнутое утверждение."""
    speech = False
    for position in range(index):
        token = clause[position]
        if token.startswith(_REPORT_VERB_PREFIXES):
            speech = True
        elif token in _INTRODUCERS and speech:
            return True
        if any(clause[position:position + 2] == list(phrase) for phrase in _REPORT_PHRASES):
            return True
    return any(
        token.startswith(_REFUTATION_PREFIXES) and _previous_word(clause, position) not in _NEGATIONS
        for position, token in enumerate(clause)
    )


def claims_decision_text(text: Any) -> bool:
    """Текст утверждает, что решение состоялось.

    Нужна утвердительная форма («договорились», «согласовано», «принято», «закрыта») без «не» перед
    ней. Утверждения нет, если:

    - в тексте есть признак отсрочки или незавершённости («отложено», «будет», «предложено»,
      «открыт», будущее время «примет», «согласует»);
    - в любом месте текста сказано, что договорённость заменена или снята: «заменена»,
      «пересмотрели», «переиграно», «устарела», «сняла», «снят», «отозвал», «аннулирована»,
      «недействительна», а также «прежняя / исходная / первоначальная …» вместе с «не действует»,
      «больше не», «уже не». Настоящее время («заменяет исходную»), полное причастие
      («пересмотренный график») и существительное рядом с утвердительной формой («договорились о
      замене») замены не означают — речь о новой, действующей версии или о предмете договорённости;
    - утвердительная форма — пересказ чужих слов: в том же предложении и до «но»/«однако» перед ней
      стоит глагол речи или просьбы (попросил, просьба, заявил, объявил, сказал, утверждает, назвал,
      сообщил, говорит), а между ними — «что», двоеточие или открывающая кавычка («попросил
      записать, что сделка закрыта»); или перед ней «со слов», «по словам», «по заявлению», «по
      мнению», «по версии»;
    - в том же предложении (до «но»/«однако») утверждение опровергнуто — «опроверг», «оспорил»,
      «отрицает», «отверг» с любой стороны от утвердительной формы, если перед словом нет «не».

    Маркер пересказа после утвердительной формы («Сделка закрыта — так сказал Чернов») утверждение
    не снимает: первым стоит вывод разбора.
    """
    tokens = words(text)
    if any(token.startswith(_DEFERRAL_PREFIXES) for token in tokens):
        return False
    if _describes_replacement(tokens):
        return False
    for sentence in _sentences(text):
        for clause in _clauses(sentence):
            for index, token in enumerate(clause):
                if token not in _AFFIRMATIVE or _previous_word(clause, index) in _NEGATIONS:
                    continue
                if not _reported(clause, index):
                    return True
    return False


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}
_SKELETON_STEPS = (
    ("shch", "s"), ("sch", "s"), ("sh", "s"), ("ch", "c"), ("zh", "z"), ("kh", "h"), ("ts", "c"), ("tz", "c"),
    ("x", "ks"), ("w", "v"), ("yu", "u"), ("ju", "u"), ("iu", "u"), ("ya", "a"), ("ja", "a"), ("ia", "a"),
    ("ye", "e"), ("yo", "e"), ("jo", "e"), ("j", "i"), ("y", "i"),
)


def skeleton(word: str) -> str:
    """Грубый «скелет» слова в латинице: одинаков для Зайцева / zaytseva / zaitseva."""
    latin = "".join(_TRANSLIT.get(char, char) for char in normalize(word).replace(" ", ""))
    for source, target in _SKELETON_STEPS:
        latin = latin.replace(source, target)
    return re.sub(r"(.)\1+", r"\1", latin)


def parse_seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))


def is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Транскрипт: строки, говорящие, поиск цитат
# ---------------------------------------------------------------------------

_LINE_RE = re.compile(
    r"^\s*(?:\[(?P<ts1>\d{1,2}:\d{2}(?::\d{2})?)\]|\((?P<ts2>\d{1,2}:\d{2}(?::\d{2})?)\)|(?P<ts3>\d{1,2}:\d{2}(?::\d{2})?))?"
    r"\s*(?:(?P<label>[^:\[\]()]{1,60}?)\s*:\s+)?(?P<text>.*?)\s*$"
)


def _plausible_label(label: str) -> bool:
    label = label.strip()
    if not label or any(char in label for char in "?!«»\"…") or ". " in label or ", " in label:
        return False
    return len(label.split()) <= 4


@dataclass
class Line:
    index: int
    number: int
    raw: str
    seconds: int | None
    timestamp: str | None
    label: str | None
    norm: str


@dataclass
class Located:
    lines: frozenset[int]
    mode: str  # exact | ellipsis | fuzzy | none
    timestamp_ok: bool | None


class Transcript:
    def __init__(self, text: str) -> None:
        self.text = text
        self.lines: list[Line] = []
        for number, raw in enumerate(text.splitlines(), start=1):
            if not raw.strip():
                continue
            match = _LINE_RE.match(raw)
            timestamp = (match.group("ts1") or match.group("ts2") or match.group("ts3")) if match else None
            label = match.group("label") if match else None
            if label and not _plausible_label(label):
                label = None
            body = raw
            if timestamp:
                body = re.sub(r"^\s*[\[(]?" + re.escape(timestamp) + r"[\])]?\s*", "", raw, count=1)
            self.lines.append(Line(len(self.lines), number, raw, parse_seconds(timestamp), timestamp, label.strip() if label else None, normalize(body)))
        self.has_timestamps = any(line.seconds is not None for line in self.lines)
        self._starts: list[int] = []
        parts: list[str] = []
        position = 0
        for line in self.lines:
            self._starts.append(position)
            parts.append(line.norm)
            position += len(line.norm) + 1
        self.full = " ".join(parts)
        self._line_tokens = [set(line.norm.split()) for line in self.lines]
        # интервал реплики: от её таймкода до таймкода следующей реплики
        self._intervals: list[tuple[int | None, int | None]] = []
        for index, line in enumerate(self.lines):
            following = next((other.seconds for other in self.lines[index + 1:] if other.seconds is not None), None)
            self._intervals.append((line.seconds, following))

    def _span_lines(self, start: int, end: int) -> frozenset[int]:
        first = bisect.bisect_right(self._starts, start) - 1
        last = bisect.bisect_right(self._starts, max(start, end - 1)) - 1
        return frozenset(range(max(first, 0), max(last, 0) + 1))

    def _occurrences(self, needle: str) -> list[frozenset[int]]:
        found: list[frozenset[int]] = []
        if not needle:
            return found
        start = self.full.find(needle)
        while start >= 0:
            end = start + len(needle)
            left_ok = start == 0 or self.full[start - 1] == " "
            right_ok = end == len(self.full) or self.full[end] == " "
            if left_ok and right_ok:
                found.append(self._span_lines(start, end))
            start = self.full.find(needle, start + 1)
        return found

    def timestamp_fits(self, lines: frozenset[int], timestamp: Any) -> bool:
        seconds = parse_seconds(timestamp)
        if seconds is None:
            return False
        for index in lines:
            begin, following = self._intervals[index]
            if begin is None:
                continue
            upper = (following if following is not None else begin) + TIMESTAMP_TOLERANCE_SEC
            if begin - TIMESTAMP_TOLERANCE_SEC <= seconds <= upper:
                return True
        return False

    def _pick(self, options: list[frozenset[int]], timestamp: Any, mode: str) -> Located:
        if timestamp is None or not self.has_timestamps:
            return Located(frozenset().union(*options), mode, None)
        fitting = [option for option in options if self.timestamp_fits(option, timestamp)]
        if fitting:
            return Located(frozenset().union(*fitting), mode, True)
        return Located(frozenset().union(*options), mode, False)

    def locate(self, quote: Any, timestamp: Any = None) -> Located:
        needle = normalize(quote)
        if not needle:
            return Located(frozenset(), "none", None)
        options = self._occurrences(needle)
        if options:
            return self._pick(options, timestamp, "exact")
        raw = str(quote)
        if "…" in raw or "..." in raw:
            pieces = [normalize(piece) for piece in re.split(r"…|\.\.\.", raw)]
            pieces = [piece for piece in pieces if piece]
            if pieces:
                located = self._ellipsis(pieces)
                if located:
                    return self._pick(located, timestamp, "ellipsis")
        return self._fuzzy(needle, timestamp)

    def _ellipsis(self, pieces: list[str]) -> list[frozenset[int]]:
        results: list[frozenset[int]] = []
        for first in self._occurrences(pieces[0]):
            lines = set(first)
            anchor = min(first)
            cursor = self._starts[anchor]
            ok = True
            for piece in pieces[1:]:
                position = self.full.find(piece, cursor)
                if position < 0:
                    ok = False
                    break
                span = self._span_lines(position, position + len(piece))
                if max(span) - anchor > 2:
                    ok = False
                    break
                lines |= span
                cursor = position + len(piece)
            if ok:
                results.append(frozenset(lines))
        return results

    def _fuzzy(self, needle: str, timestamp: Any) -> Located:
        quote_tokens = {token for token in needle.split() if len(token) >= 3 or token.isdigit()}
        if len(quote_tokens) < 3:
            return Located(frozenset(), "none", None)
        scored: list[tuple[float, frozenset[int]]] = []
        for index in range(len(self.lines)):
            for width in (1, 2):
                if index + width > len(self.lines):
                    continue
                tokens = set().union(*self._line_tokens[index:index + width])
                share = len(quote_tokens & tokens) / len(quote_tokens)
                if share >= FUZZY_MIN_SHARE:
                    scored.append((share - 0.01 * (width - 1), frozenset(range(index, index + width))))
        if not scored:
            return Located(frozenset(), "none", None)
        best = max(score for score, _ in scored)
        options = [lines for score, lines in scored if score >= best - 1e-9]
        return self._pick(options, timestamp, "fuzzy")

    def speakers_of(self, lines: frozenset[int]) -> set[str]:
        return {self.lines[index].label for index in lines if self.lines[index].label}

    def describe(self, lines: frozenset[int] | set[int]) -> str:
        parts = []
        for index in sorted(lines):
            line = self.lines[index]
            parts.append(line.timestamp or f"строка {line.number}")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Люди ядра
# ---------------------------------------------------------------------------


class People:
    def __init__(self, core_people: list[dict[str, Any]], participants: list[dict[str, Any]]) -> None:
        self.entries = {entry["id"]: entry for entry in core_people if isinstance(entry, dict) and entry.get("id")}
        self.participants = {item.get("person_id"): item for item in participants if isinstance(item, dict) and item.get("person_id")}

    def names(self, person_id: str, with_variants: bool = True) -> list[str]:
        entry = self.entries.get(person_id) or {}
        values = list(entry.get("names") or []) + list(entry.get("labels") or [])
        if with_variants:
            values += list(entry.get("asr_variants") or [])
        return [value for value in values if isinstance(value, str) and value.strip()]

    def is_present(self, person_id: str) -> bool:
        return bool((self.entries.get(person_id) or {}).get("present", True))

    def display(self, person_id: str | None) -> str:
        if not person_id:
            return "—"
        names = self.names(person_id, with_variants=False)
        return names[0] if names else person_id

    def _match_text(self, text: str, with_variants: bool = True) -> set[str]:
        tokens = set(words(text))
        result = set()
        if not tokens:
            return result
        for person_id in self.entries:
            for name in self.names(person_id, with_variants):
                name_tokens = words(name)
                if name_tokens and set(name_tokens) <= tokens:
                    result.add(person_id)
                    break
        return result

    def _match_slug(self, value: str) -> set[str]:
        slug_parts = {skeleton(part) for part in re.split(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", value) if part}
        result = set()
        for person_id in self.entries:
            for name in self.names(person_id, with_variants=False):
                name_parts = [skeleton(part) for part in words(name)]
                if not name_parts:
                    continue
                if set(name_parts) <= slug_parts or (len(name_parts) >= 2 and len(name_parts[-1]) >= 4 and name_parts[-1] in slug_parts):
                    result.add(person_id)
                    break
        return result

    def resolve(self, value: Any) -> set[str]:
        """Люди ядра, которым соответствует значение поля (id, имя или метка спикера)."""
        if not isinstance(value, str) or not value.strip():
            return set()
        direct = {person_id for person_id, entry in self.entries.items() if value == person_id or value in (entry.get("ids") or [])}
        if direct:
            return direct
        participant = self.participants.get(value)
        if participant:
            by_name = self._match_text(str(participant.get("full_name") or ""))
            if by_name:
                return by_name
        by_text = self._match_text(value)
        if by_text:
            return by_text
        return self._match_slug(value)

    def is_participant_value(self, value: Any) -> bool:
        """Значение указывает на участника встречи (по 01 или по присутствующему человеку ядра)."""
        if isinstance(value, str) and value in self.participants:
            return True
        return any(self.is_present(person_id) for person_id in self.resolve(value))


# ---------------------------------------------------------------------------
# Находки разбора
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    uid: str
    artifact: str
    path: str
    source: str
    data: Any
    text: str
    quotes: list[tuple[str, Any]]
    tokens: list[str] = field(default_factory=list)
    lines: frozenset[int] = frozenset()
    support: str = "n/a"  # exact | ellipsis | fuzzy | none | n/a
    timestamp_ok: bool | None = None
    located: list[dict[str, Any]] = field(default_factory=list)
    prior_lines: frozenset[int] = frozenset()

    @property
    def finding_id(self) -> str | None:
        if isinstance(self.data, dict):
            return self.data.get("id") or self.data.get("person_id")
        return None

    def summary(self) -> str:
        label = self.text if len(self.text) <= 140 else self.text[:137] + "…"
        return label or self.uid

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "artifact": self.artifact,
            "path": self.path,
            "kind": self.source,
            "id": self.finding_id,
            "summary": self.summary(),
            "quotes": self.located,
            "support": self.support,
        }


_SUPPORT_RANK = {"none": 0, "fuzzy": 1, "ellipsis": 2, "exact": 3}


def _join(*values: Any) -> str:
    parts = []
    for value in values:
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))
    return " · ".join(parts)


def collect_findings(artifacts: dict[str, Any], transcript: Transcript) -> list[Finding]:
    findings: list[Finding] = []

    def add(artifact: str, path: str, source: str, data: Any, text: str, quotes: list[tuple[str, Any]], prior: tuple[str, Any] | None = None) -> None:
        finding = Finding(f"{artifact}#{path}", artifact, path, source, data, text, [(q, ts) for q, ts in quotes if isinstance(q, str) and q.strip()])
        finding.tokens = words(text)
        lines: set[int] = set()
        ranks: list[int] = []
        stamps: list[bool] = []
        for quote, timestamp in finding.quotes:
            located = transcript.locate(quote, timestamp)
            lines |= located.lines
            ranks.append(_SUPPORT_RANK[located.mode])
            if located.timestamp_ok is not None:
                stamps.append(located.timestamp_ok)
            finding.located.append({"quote": quote, "timestamp": timestamp, "support": located.mode, "at": transcript.describe(located.lines) or None})
        finding.lines = frozenset(lines)
        if ranks:
            finding.support = {value: key for key, value in _SUPPORT_RANK.items()}[min(ranks)]
        finding.timestamp_ok = all(stamps) if stamps else None
        if prior and isinstance(prior[0], str) and prior[0].strip():
            finding.prior_lines = transcript.locate(prior[0], prior[1]).lines
        findings.append(finding)

    extraction = artifacts.get("01_extraction.json")
    if isinstance(extraction, dict):
        for index, item in enumerate(extraction.get("key_facts") or []):
            if isinstance(item, dict):
                add("01_extraction.json", f"key_facts[{index}]", "key_fact", item, _join(item.get("fact")), [(item.get("quote"), item.get("timestamp"))])
        for index, item in enumerate(extraction.get("topics") or []):
            if isinstance(item, dict):
                add("01_extraction.json", f"topics[{index}]", "topic", item, _join(item.get("title")), [(item.get("source_quote"), item.get("source_timestamp"))])
        for index, item in enumerate(extraction.get("participants") or []):
            if isinstance(item, dict):
                add("01_extraction.json", f"participants[{index}]", "participant", item, _join(item.get("full_name")), [])
    power = artifacts.get("05_power.json")
    if isinstance(power, dict):
        for index, item in enumerate(power.get("concessions") or []):
            if isinstance(item, dict):
                add("05_power.json", f"concessions[{index}]", "concession", item, _join(item.get("quote")), [(item.get("quote"), item.get("timestamp"))], (item.get("prior_position_quote"), item.get("prior_position_timestamp")))
    commitments = artifacts.get("07_commitments.json")
    if isinstance(commitments, dict):
        for index, item in enumerate(commitments.get("commitments") or []):
            if isinstance(item, dict):
                add("07_commitments.json", f"commitments[{index}]", "commitment", item, _join(item.get("what")), [(item.get("source_quote"), item.get("source_timestamp"))])
        for index, item in enumerate(commitments.get("open_questions") or []):
            if isinstance(item, dict):
                add("07_commitments.json", f"open_questions[{index}]", "open_question", item, _join(item.get("question"), item.get("topic")), [(item.get("quote"), item.get("timestamp"))])
    risks = artifacts.get("08_risks.json")
    if isinstance(risks, dict):
        for index, item in enumerate(risks.get("risks") or []):
            if isinstance(item, dict):
                mitigation = item.get("mitigation") if isinstance(item.get("mitigation"), dict) else {}
                evidence = [(entry.get("quote"), entry.get("timestamp")) for entry in item.get("evidence") or [] if isinstance(entry, dict)]
                add("08_risks.json", f"risks[{index}]", "risk", item, _join(item.get("description"), mitigation.get("planned_actions")), evidence)
    authority = artifacts.get("09_decision_authority.json")
    if isinstance(authority, dict):
        for index, item in enumerate(authority.get("decision_areas") or []):
            if isinstance(item, dict):
                evidence = [(entry.get("quote"), entry.get("timestamp")) for entry in item.get("evidence") or [] if isinstance(entry, dict)]
                add("09_decision_authority.json", f"decision_areas[{index}]", "decision_area", item, _join(item.get("topic"), item.get("decision"), item.get("current_state"), item.get("options")), evidence)
        for index, item in enumerate(authority.get("authority_observations") or []):
            if isinstance(item, dict):
                evidence = [(entry.get("quote"), entry.get("timestamp")) for entry in item.get("evidence") or [] if isinstance(entry, dict)]
                add("09_decision_authority.json", f"authority_observations[{index}]", "authority_observation", item, _join(item.get("observed_role"), item.get("formal_authority_in_decision")), evidence)
        for index, item in enumerate(authority.get("unresolved") or []):
            if isinstance(item, str):
                add("09_decision_authority.json", f"unresolved[{index}]", "unresolved", item, item, [])
    return findings


# Обязательство «принять решение / ответить / вынести на рассмотрение» честно отражает отложенное решение.
_PENDING_DECISION_PREFIXES = ("решени", "решить", "ответ", "рассмотр", "вынест", "вынес")


def pending_decision_commitment(finding: Finding) -> bool:
    data = finding.data if isinstance(finding.data, dict) else {}
    if finding.source != "commitment" or data.get("status") in ("done", "agreed", "formalized"):
        return False
    return any(token.startswith(_PENDING_DECISION_PREFIXES) for token in words(data.get("what")))


def claims_agreement(finding: Finding) -> bool:
    """Находка утверждает, что договорённость или решение состоялись."""
    data = finding.data if isinstance(finding.data, dict) else {}
    if finding.source == "commitment":
        return data.get("status") != "cancelled" and not pending_decision_commitment(finding)
    if finding.source == "topic":
        return data.get("status") in ("agreed", "formalized", "done")
    if finding.source == "decision_area":
        return claims_decision_text(data.get("current_state"))
    if finding.source == "key_fact":
        return claims_decision_text(data.get("fact"))
    return False


def reports_open(finding: Finding) -> bool:
    """Находка может служить честным отражением нерешённого вопроса."""
    data = finding.data if isinstance(finding.data, dict) else {}
    if finding.source in ("open_question", "unresolved"):
        return True
    if finding.source == "commitment":
        return data.get("status") == "cancelled" or pending_decision_commitment(finding)
    if finding.source in ("decision_area", "key_fact", "topic"):
        return not claims_agreement(finding)
    return False


# ---------------------------------------------------------------------------
# Ядро: загрузка и проверка
# ---------------------------------------------------------------------------


def _anchors(item: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = item.get(key) or []
    return [anchor for anchor in value if isinstance(anchor, dict)]


def _people_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def check_core(core: Any, transcript_text: str | None = None) -> list[str]:
    """Структурная проверка core.json; с транскриптом — ещё и что каждая опорная цитата в нём есть."""
    errors: list[str] = []
    if not isinstance(core, dict):
        return ["core.json: корень должен быть объектом"]
    people = core.get("people", [])
    if not isinstance(people, list):
        errors.append("people: ожидается список")
        people = []
    person_ids: set[str] = set()
    for index, entry in enumerate(people):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
            errors.append(f"people[{index}]: нужен непустой id")
            continue
        if entry["id"] in person_ids:
            errors.append(f"people[{index}]: повтор id {entry['id']!r}")
        person_ids.add(entry["id"])
        if not [name for name in entry.get("names") or [] if isinstance(name, str) and name.strip()] and not entry.get("labels"):
            errors.append(f"people[{index}] {entry['id']}: нужны names или labels")
        for key in ("names", "labels", "asr_variants", "ids"):
            if key in entry and not (isinstance(entry[key], list) and all(isinstance(value, str) for value in entry[key])):
                errors.append(f"people[{index}].{key}: ожидается список строк")
        if "present" in entry and not isinstance(entry["present"], bool):
            errors.append(f"people[{index}].present: ожидается true/false")
    for key in ("non_human_speakers",):
        if key in core and not (isinstance(core[key], list) and all(isinstance(value, str) for value in core[key])):
            errors.append(f"{key}: ожидается список строк")
    items = core.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items: нужен непустой список пунктов")
        return errors
    transcript = Transcript(transcript_text) if transcript_text is not None else None
    if transcript is not None and core.get("source_sha256"):
        digest = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
        if digest != core["source_sha256"]:
            errors.append("source_sha256 не совпадает с транскриптом: ядро зафиксировано для другого источника")
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: ожидается объект")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"{where}: нужен id")
        elif item_id in seen:
            errors.append(f"{where}: повтор id {item_id!r}")
        else:
            seen.add(item_id)
            where = f"{where} {item_id}"
        kind = item.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}: kind {kind!r} не из {', '.join(KINDS)}")
            continue
        if item.get("level", "core") not in LEVELS:
            errors.append(f"{where}: level — core или supporting")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            errors.append(f"{where}: нужен title — что этот пункт значит для руководителя")
        for key in ("keywords", "expect_text", "forbid_text"):
            value = item.get(key)
            if value is None:
                continue
            if not (isinstance(value, list) and value and all(isinstance(group, list) and group and all(isinstance(alt, str) and words(alt) for alt in group) for group in value)):
                errors.append(f"{where}.{key}: ожидается список групп альтернатив, например [[\"смет\"], [\"три склад\", \"трех склад\"]]")
        for key in ("anchors", "related", "superseded", "prior_anchor"):
            value = item.get(key)
            if value is None:
                continue
            if not isinstance(value, list) or not all(isinstance(anchor, dict) and isinstance(anchor.get("quote"), str) and anchor["quote"].strip() for anchor in value):
                errors.append(f"{where}.{key}: ожидается список {{quote, timestamp}}")
                continue
            for anchor in value:
                timestamp = anchor.get("timestamp")
                if timestamp is not None and parse_seconds(timestamp) is None:
                    errors.append(f"{where}.{key}: некорректный таймкод {timestamp!r}")
                if transcript is not None:
                    located = transcript.locate(anchor["quote"], timestamp)
                    if located.mode != "exact":
                        errors.append(f"{where}.{key}: цитаты нет в транскрипте дословно: {anchor['quote'][:80]!r}")
                    elif located.timestamp_ok is False:
                        errors.append(f"{where}.{key}: таймкод {timestamp} не совпадает с репликой цитаты {anchor['quote'][:60]!r}")
        for key in ("who", "to_whom", "owner", "forbidden_people"):
            for ref in _people_refs(item.get(key)):
                if ref not in person_ids:
                    errors.append(f"{where}.{key}: человек {ref!r} не описан в people")
        if "person" in item and item["person"] is not None and item["person"] not in person_ids:
            errors.append(f"{where}.person: человек {item['person']!r} не описан в people")
        if "deadline" in item and item["deadline"] is not None and not is_iso_date(item["deadline"]):
            errors.append(f"{where}.deadline: ожидается YYYY-MM-DD или null")
        if "deadline_verbatim" in item and not (isinstance(item["deadline_verbatim"], list) and all(isinstance(alt, str) and words(alt) for alt in item["deadline_verbatim"])):
            errors.append(f"{where}.deadline_verbatim: ожидается список вариантов")
        if "confidence_max" in item and item["confidence_max"] not in CONFIDENCE_ORDER:
            errors.append(f"{where}.confidence_max: одно из {', '.join(CONFIDENCE_ORDER)}")
        if "severity_min" in item and item["severity_min"] not in SEVERITY_ORDER:
            errors.append(f"{where}.severity_min: одно из {', '.join(SEVERITY_ORDER)}")
        if kind in ("commitment", "concession") and not _people_refs(item.get("who")):
            errors.append(f"{where}: для {kind} нужен who")
        if kind in ("commitment", "risk", "concession", "agreed_term") and not _anchors(item, "anchors"):
            errors.append(f"{where}: для {kind} нужна хотя бы одна опорная цитата в anchors")
        if kind in ("authority", "not_decision", "open_question") and not (_anchors(item, "anchors") or item.get("keywords")):
            errors.append(f"{where}: для {kind} нужны anchors или keywords")
        if kind == "authority" and "person" not in item:
            errors.append(f"{where}: для authority нужен person (id человека ядра или null, если ЛПР на встрече не назван)")
        if kind == "participant" and (not isinstance(item.get("person"), str) or "confirmed" not in item):
            errors.append(f"{where}: для participant нужны person и confirmed")
        if kind == "outcome":
            allowed = item.get("outcome_any")
            if not (isinstance(allowed, list) and allowed and all(value in OUTCOMES for value in allowed)):
                errors.append(f"{where}: outcome_any — непустой список из {', '.join(OUTCOMES)}")
    return errors


def load_core(path: Path) -> dict[str, Any]:
    try:
        core = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise NaError(f"Файл ядра не найден: {path}") from None
    except json.JSONDecodeError as exc:
        raise NaError(f"Некорректный JSON ядра {path}: {exc}") from None
    errors = check_core(core)
    if errors:
        raise NaError("core.json не прошёл проверку структуры: " + "; ".join(errors[:12]))
    return core


# ---------------------------------------------------------------------------
# Оценка
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    status: str
    reasons: list[str] = field(default_factory=list)

    def worsen(self, status: str, reason: str) -> None:
        self.reasons.append(reason)
        if STATUS_RANK[status] < STATUS_RANK[self.status]:
            self.status = status


@dataclass
class ItemContext:
    item: dict[str, Any]
    main: frozenset[int]
    related: frozenset[int]
    superseded: frozenset[int]
    prior: frozenset[int]


def _item_lines(transcript: Transcript, item: dict[str, Any], key: str) -> frozenset[int]:
    lines: set[int] = set()
    for anchor in _anchors(item, key):
        lines |= transcript.locate(anchor["quote"], anchor.get("timestamp")).lines
    return frozenset(lines)


def _hits(finding: Finding, context: ItemContext) -> dict[str, bool]:
    return {
        "main": bool(finding.lines & context.main),
        "related": bool(finding.lines & context.related),
        "superseded": bool(finding.lines & context.superseded),
    }


def _score(hits: dict[str, bool], keyword_hit: bool) -> int:
    return 8 * hits["main"] + 4 * keyword_hit + 2 * hits["related"] + 1 * hits["superseded"]


def _who_check(verdict: Verdict, people: People, value: Any, expected: list[str], role: str, missing_status: str = "PARTIAL") -> None:
    if not expected:
        return
    expected_names = " / ".join(people.display(person_id) for person_id in expected)
    if not isinstance(value, str) or not value.strip():
        verdict.worsen(missing_status, f"{role} не указан (ожидается {expected_names})")
        return
    resolved = people.resolve(value)
    if resolved & set(expected):
        if len(resolved) > 1:
            verdict.worsen("PARTIAL", f"{role} {value!r} неоднозначен: подходит нескольким людям ядра")
        return
    if resolved:
        verdict.worsen("DISTORTED", f"не тот {role}: {', '.join(people.display(item) for item in sorted(resolved))} вместо {expected_names}")
    else:
        verdict.worsen("DISTORTED", f"{role} {value!r} не сопоставлен с {expected_names}")


def _anchor_check(verdict: Verdict, hits: dict[str, bool], keyword_hit: bool, item: dict[str, Any]) -> None:
    if hits["main"]:
        return
    if hits["related"]:
        verdict.worsen("PARTIAL", "опора — сопутствующая реплика, а не та, где выбор состоялся")
    elif hits["superseded"]:
        verdict.worsen("PARTIAL", "опора — реплика, которую позже на встрече переиграли")
    elif _anchors(item, "anchors"):
        verdict.worsen("PARTIAL", "опорная цитата не совпадает с опорной репликой ядра (найдено по формулировке)")


def _support_check(verdict: Verdict, finding: Finding) -> None:
    if finding.support == "none":
        verdict.worsen("DISTORTED", "цитаты находки нет в источнике")
    elif finding.support == "fuzzy":
        verdict.worsen("PARTIAL", "цитата находки не дословна (найдена только по словам)")
    if finding.timestamp_ok is False:
        verdict.worsen("PARTIAL", "таймкод находки не совпадает с репликой цитаты")


def _text_checks(verdict: Verdict, finding: Finding, item: dict[str, Any]) -> None:
    tokens = finding.tokens
    absent = missing_groups(tokens, item.get("expect_text"))
    if absent:
        verdict.worsen("PARTIAL", "в формулировке нет ожидаемого: " + "; ".join(" / ".join(group) for group in absent))
    forbidden = present_alternatives(tokens, item.get("forbid_text"))
    if forbidden:
        verdict.worsen("DISTORTED", "в формулировке устаревшее или искажённое значение: " + ", ".join(forbidden))


def _confidence_check(verdict: Verdict, finding: Finding, item: dict[str, Any]) -> None:
    limit = item.get("confidence_max")
    value = finding.data.get("confidence") if isinstance(finding.data, dict) else None
    if limit and value in CONFIDENCE_ORDER and CONFIDENCE_ORDER[value] > CONFIDENCE_ORDER[limit]:
        verdict.worsen("PARTIAL", f"уверенность {value} выше допустимой для этого пункта ({limit})")


def _deadline_check(verdict: Verdict, actual: Any, verbatim: Any, item: dict[str, Any], role: str = "срок") -> None:
    if "deadline" not in item and "deadline_verbatim" not in item:
        return
    expected = item.get("deadline")
    variants = item.get("deadline_verbatim") or []
    verbatim_tokens = words(verbatim)
    verbatim_ok = bool(variants) and any(phrase_in(verbatim_tokens, variant) for variant in variants)
    if expected:
        if actual == expected:
            return
        if is_iso_date(actual):
            verdict.worsen("DISTORTED", f"{role} искажён: {actual} вместо {expected}")
        elif verbatim_ok:
            verdict.worsen("PARTIAL", f"{role} только словами, без даты {expected}")
        else:
            verdict.worsen("PARTIAL", f"без срока (ожидается {expected})")
        return
    # дата в разговоре не названа: ожидается deadline null и срок словами
    if is_iso_date(actual):
        verdict.worsen("PARTIAL", f"{role} {actual} вычислен из относительного срока; в разговоре даты нет")
    if variants and not verbatim_ok:
        verdict.worsen("PARTIAL", f"без срока словами (ожидается: {' / '.join(variants)})")


def _evaluate_pair(item: dict[str, Any], finding: Finding, hits: dict[str, bool], keyword_hit: bool, context: ItemContext, people: People) -> Verdict:
    kind = item["kind"]
    data = finding.data if isinstance(finding.data, dict) else {}
    verdict = Verdict("FULL")
    _support_check(verdict, finding)
    _text_checks(verdict, finding, item)
    if kind == "commitment":
        _anchor_check(verdict, hits, keyword_hit, item)
        _who_check(verdict, people, data.get("who"), _people_refs(item.get("who")), "исполнитель")
        if item.get("to_whom"):
            to_whom = data.get("to_whom")
            resolved = people.resolve(to_whom) if to_whom else set()
            if to_whom and resolved and not (resolved & set(_people_refs(item.get("to_whom")))):
                verdict.worsen("PARTIAL", f"не тот получатель: {', '.join(people.display(item) for item in sorted(resolved))}")
        _deadline_check(verdict, data.get("deadline"), data.get("deadline_verbatim"), item)
        allowed = item.get("status_any")
        status = data.get("status")
        if allowed and status not in allowed:
            verdict.worsen("DISTORTED", f"статус {status!r} вместо одного из {', '.join(allowed)}")
        elif not allowed and status in ("done", "cancelled"):
            verdict.worsen("DISTORTED", f"статус {status!r}: обязательство показано закрытым")
        _confidence_check(verdict, finding, item)
    elif kind == "risk":
        _anchor_check(verdict, hits, keyword_hit, item)
        mitigation = data.get("mitigation") if isinstance(data.get("mitigation"), dict) else {}
        _who_check(verdict, people, mitigation.get("owner"), _people_refs(item.get("owner")), "владелец риска")
        minimum = item.get("severity_min")
        severity = data.get("severity")
        if minimum and severity in SEVERITY_ORDER and SEVERITY_ORDER[severity] < SEVERITY_ORDER[minimum]:
            verdict.worsen("PARTIAL", f"важность {severity} ниже ожидаемой ({minimum})")
        if data.get("status") in ("closed",):
            verdict.worsen("DISTORTED", "риск показан закрытым")
        _confidence_check(verdict, finding, item)
    elif kind == "concession":
        _anchor_check(verdict, hits, keyword_hit, item)
        if finding.source == "concession":
            _who_check(verdict, people, data.get("speaker"), _people_refs(item.get("who")), "уступивший")
            if context.prior and not (finding.prior_lines & context.prior):
                verdict.worsen("PARTIAL", "прежняя позиция не указана или указана не та")
        else:
            verdict.worsen("PARTIAL", "уступка отражена только как факт, без прежней позиции и того, кто уступил")
    elif kind == "agreed_term":
        _anchor_check(verdict, hits, keyword_hit, item)
        if finding.source == "decision_area" and not claims_agreement(finding):
            verdict.worsen("PARTIAL", "принятое условие показано в карте решений как неоконченное")
        if finding.source == "topic" and not claims_agreement(finding):
            verdict.worsen("PARTIAL", "принятое условие показано темой без статуса agreed")
        if finding.source == "commitment" and data.get("status") == "cancelled":
            verdict.worsen("DISTORTED", "принятое условие показано отменённым")
        _confidence_check(verdict, finding, item)
    elif kind == "authority":
        expected = item.get("person")
        forbidden = set(_people_refs(item.get("forbidden_people")))
        if finding.source == "decision_area":
            value = data.get("final_decision_maker")
            resolved = people.resolve(value) if value else set()
            if value and resolved & forbidden:
                verdict.worsen("DISTORTED", f"ЛПР указан неверно: {', '.join(people.display(item) for item in sorted(resolved))}")
            elif expected and people.is_present(expected):
                if not value:
                    verdict.worsen("PARTIAL", f"ЛПР не указан (по разговору — {people.display(expected)})")
                elif expected not in resolved:
                    verdict.worsen("DISTORTED", f"ЛПР указан неверно: {value!r} вместо {people.display(expected)}")
            else:
                # ЛПР на встрече отсутствует или не назван
                if value and expected and expected in resolved:
                    pass
                elif value and people.is_participant_value(value):
                    verdict.worsen("DISTORTED", f"ЛПР назначен из участников встречи без опоры: {people.display(next(iter(resolved))) if resolved else value}")
            _anchor_check(verdict, hits, keyword_hit, item)
        elif finding.source == "authority_observation":
            resolved = people.resolve(data.get("person_id"))
            if expected and expected in resolved:
                _anchor_check(verdict, hits, keyword_hit, item)
            elif resolved & forbidden:
                verdict.worsen("DISTORTED", "полномочие приписано не тому человеку")
            else:
                verdict.worsen("PARTIAL", "наблюдение о полномочиях не о том человеке")
        else:  # unresolved
            if expected and people.is_present(expected):
                verdict.worsen("PARTIAL", "вопрос о ЛПР оставлен нерешённым, хотя в разговоре ЛПР назван")
    return verdict


def _assign_positive(items: list[dict[str, Any]], contexts: dict[str, ItemContext], findings: list[Finding], people: People) -> dict[str, tuple[Finding, Verdict, dict[str, bool]]]:
    pairs = []
    for item in items:
        kind = item["kind"]
        if kind not in SOURCES:
            continue
        context = contexts[item["id"]]
        keywords = item.get("keywords")
        for finding in findings:
            if finding.source not in SOURCES[kind]:
                continue
            hits = _hits(finding, context)
            keyword_hit = groups_hit(finding.tokens, keywords)
            if keywords:
                candidate = hits["main"] or keyword_hit
            else:
                candidate = any(hits.values())
            if kind == "authority" and finding.source == "unresolved":
                candidate = keyword_hit
            if not candidate:
                continue
            verdict = _evaluate_pair(item, finding, hits, keyword_hit, context, people)
            pairs.append((STATUS_RANK[verdict.status], _score(hits, keyword_hit), item["id"], finding, verdict, hits))
    pairs.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    by_id = {item["id"]: item for item in items}
    assigned: dict[str, tuple[Finding, Verdict, dict[str, bool]]] = {}
    used: set[tuple[str, str]] = set()
    for _, _, item_id, finding, verdict, hits in pairs:
        if item_id in assigned:
            continue
        kind = by_id[item_id]["kind"]
        key = (kind, finding.uid)
        if kind in EXCLUSIVE_KINDS and key in used:
            continue
        assigned[item_id] = (finding, verdict, hits)
        used.add(key)
    return assigned


def _evaluate_open(item: dict[str, Any], context: ItemContext, findings: list[Finding], assigned_uids: set[str], people: People) -> tuple[Verdict, list[Finding], list[Finding]]:
    keywords = item.get("keywords")
    claims: list[Finding] = []
    reports: list[Finding] = []
    for finding in findings:
        hits = _hits(finding, context)
        anchor_hit = any(hits.values())
        keyword_hit = groups_hit(finding.tokens, keywords)
        if claims_agreement(finding):
            if finding.uid not in assigned_uids:
                # опора на решающую реплику пункта — ложное «договорились» при любой формулировке;
                # опора на сопутствующую реплику — только если совпадает и предмет (ключевые слова)
                false_claim = hits["main"] or (anchor_hit and (keyword_hit or not keywords))
            else:
                # находка уже закрывает другой пункт ядра, но заодно объявляет решённым и этот предмет
                false_claim = anchor_hit and bool(keywords) and keyword_hit
            if false_claim:
                claims.append(finding)
                continue
        if reports_open(finding):
            if (anchor_hit and (keyword_hit or not keywords or hits["main"])) or keyword_hit:
                reports.append(finding)
    must_report = True if item["kind"] == "open_question" else bool(item.get("must_be_reported", True))
    if claims:
        verdict = Verdict("DISTORTED")
        for finding in claims:
            verdict.reasons.append(f"ложное «договорились»: {finding.uid} — {finding.summary()}")
        return verdict, claims, reports
    if reports:
        verdict = Verdict("FULL")
        if item.get("deadline"):
            areas = [finding for finding in reports if finding.source == "decision_area"]
            if areas:
                deadlines = {finding.data.get("deadline") for finding in areas}
                if item["deadline"] not in deadlines:
                    actual = next((value for value in deadlines if is_iso_date(value)), None)
                    if actual:
                        verdict.worsen("DISTORTED", f"срок решения искажён: {actual} вместо {item['deadline']}")
                    else:
                        verdict.worsen("PARTIAL", f"срок решения не указан (ожидается {item['deadline']})")
        if all(finding.support == "none" for finding in reports if finding.quotes) and any(finding.quotes for finding in reports):
            verdict.worsen("PARTIAL", "цитаты находок не найдены в источнике")
        return verdict, claims, reports
    if not must_report:
        return Verdict("FULL", ["не выдано за решение; отдельного упоминания не требуется"]), claims, reports
    return Verdict("MISSING", ["не отражено ни открытым вопросом, ни нерешённым пунктом карты решений"]), claims, reports


def _evaluate_participant(item: dict[str, Any], findings: list[Finding], people: People) -> tuple[Verdict, list[Finding]]:
    person = item["person"]
    canonical = [name for name in people.names(person, with_variants=False)]
    variants = list((people.entries.get(person) or {}).get("asr_variants") or [])
    matched: list[tuple[Finding, bool]] = []
    for finding in findings:
        if finding.source != "participant":
            continue
        data = finding.data
        tokens = set(words(data.get("full_name")))
        by_canonical = any(words(name) and set(words(name)) <= tokens for name in canonical)
        by_variant = any(words(name) and set(words(name)) <= tokens for name in variants)
        by_id = data.get("person_id") == person or data.get("person_id") in ((people.entries.get(person) or {}).get("ids") or [])
        if by_canonical or by_variant or by_id:
            matched.append((finding, by_canonical or by_id))
    if not matched:
        return Verdict("MISSING", ["участник не найден в 01_extraction.participants"]), []
    verdict = Verdict("FULL")
    if len(matched) > 1:
        verdict.worsen("DISTORTED", "один человек записан несколькими участниками: " + ", ".join(str(finding.data.get("full_name")) for finding, _ in matched))
    finding, canonical_hit = matched[0]
    if not canonical_hit:
        verdict.worsen("PARTIAL", f"имя записано в варианте распознавания: {finding.data.get('full_name')!r}")
    if item.get("confirmed") is False:
        if finding.data.get("identity_source") in ("user_confirmed", "user_confirmation"):
            verdict.worsen("DISTORTED", "личность показана подтверждённой пользователем, хотя подтверждения нет")
        if finding.data.get("confidence") == "certain":
            verdict.worsen("PARTIAL", "уверенность certain для неподтверждённой личности")
    return verdict, [finding for finding, _ in matched]


def _non_human(transcript: Transcript, finding: Finding, labels: set[str]) -> bool:
    if not labels or not finding.lines:
        return False
    speakers = {normalize(label) for label in transcript.speakers_of(finding.lines)}
    return bool(speakers) and speakers <= labels


def evaluate(core: dict[str, Any], transcript_text: str, artifacts: dict[str, Any]) -> dict[str, Any]:
    """Чистая функция оценки: ядро + транскрипт + артефакты → результат (без записи файлов)."""
    transcript = Transcript(transcript_text)
    extraction = artifacts.get("01_extraction.json") if isinstance(artifacts.get("01_extraction.json"), dict) else {}
    people = People(core.get("people") or [], extraction.get("participants") or [])
    findings = collect_findings(artifacts, transcript)
    items = [item for item in core["items"] if isinstance(item, dict)]
    contexts = {
        item["id"]: ItemContext(item, _item_lines(transcript, item, "anchors"), _item_lines(transcript, item, "related"), _item_lines(transcript, item, "superseded"), _item_lines(transcript, item, "prior_anchor"))
        for item in items
    }
    assigned = _assign_positive(items, contexts, findings, people)
    assigned_uids = {finding.uid for finding, _, _ in assigned.values()}

    results: list[dict[str, Any]] = []
    extras: dict[str, dict[str, Any]] = {}

    def add_extra(finding: Finding, status: str, reason: str, item_id: str | None) -> None:
        entry = extras.get(finding.uid)
        if entry is None:
            entry = {**finding.as_dict(), "status": status, "reasons": [], "related_items": []}
            extras[finding.uid] = entry
        if STATUS_RANK.get(status, 9) < STATUS_RANK.get(entry["status"], 9):
            entry["status"] = status
        entry["reasons"].append(reason)
        if item_id and item_id not in entry["related_items"]:
            entry["related_items"].append(item_id)

    for item in items:
        kind = item["kind"]
        context = contexts[item["id"]]
        expected_anchor = (_anchors(item, "anchors") or [None])[0]
        base = {
            "id": item["id"],
            "kind": kind,
            "level": item.get("level", "core"),
            "title": item["title"],
            "expected_anchor": expected_anchor,
        }
        if kind in SOURCES:
            if item["id"] in assigned:
                finding, verdict, _ = assigned[item["id"]]
                results.append({**base, "status": verdict.status, "reasons": verdict.reasons or ["совпадает с ядром и опорой"], "findings": [finding.as_dict()]})
            else:
                results.append({**base, "status": "MISSING", "reasons": ["соответствующей находки нет"], "findings": []})
        elif kind in ("not_decision", "open_question"):
            verdict, claims, reports = _evaluate_open(item, context, findings, assigned_uids, people)
            for finding in claims:
                add_extra(finding, "DISTORTED", f"ложное «договорились»: противоречит пункту ядра {item['id']} ({item['title']})", item["id"])
            shown = claims or reports
            results.append({**base, "status": verdict.status, "reasons": verdict.reasons or ["отражено как нерешённое"], "findings": [finding.as_dict() for finding in shown]})
        elif kind == "participant":
            verdict, matched = _evaluate_participant(item, findings, people)
            results.append({**base, "status": verdict.status, "reasons": verdict.reasons or ["участник записан верно"], "findings": [finding.as_dict() for finding in matched]})
        elif kind == "outcome":
            value = extraction.get("outcome") if extraction else None
            if "01_extraction.json" not in artifacts:
                verdict = Verdict("MISSING", ["нет 01_extraction.json"])
            elif value in item["outcome_any"]:
                verdict = Verdict("FULL", [f"исход {value}"])
            elif value is None:
                verdict = Verdict("MISSING", ["исход не указан"])
            else:
                verdict = Verdict("DISTORTED", [f"исход {value!r} вместо одного из {', '.join(item['outcome_any'])}"])
            results.append({**base, "status": verdict.status, "reasons": verdict.reasons, "findings": []})

    # Лишние находки: дубли и устаревшие версии реестровых записей.
    # Обязательство — дубль, если опирается на опорную или сопутствующую реплику пункта (повтор в
    # итогах), называет его предмет и того же исполнителя. Риск — только при опоре на опорную реплику:
    # сопутствующая реплика одного риска (последствие, ответ владельца) часто служит доказательством
    # другого риска, и совпадение ключевого слова в описании или мерах дублем его не делает.
    by_item = {item["id"]: item for item in items}
    for item_id, (finding, _, _) in assigned.items():
        item = by_item[item_id]
        if item["kind"] not in ("commitment", "risk"):
            continue
        context = contexts[item_id]
        expected_who = set(_people_refs(item.get("who")))
        for other in findings:
            if other.source != finding.source or other.uid in assigned_uids:
                continue
            hits = _hits(other, context)
            keyword_ok = groups_hit(other.tokens, item.get("keywords")) or not item.get("keywords")
            if not keyword_ok:
                continue
            if item["kind"] == "commitment" and expected_who and not (people.resolve(other.data.get("who")) & expected_who):
                continue
            duplicate = hits["main"] if item["kind"] == "risk" else hits["main"] or hits["related"]
            if duplicate:
                add_extra(other, "DISTORTED", f"дубль пункта ядра {item_id}: та же договорённость записана повторно", item_id)
            elif hits["superseded"]:
                add_extra(other, "DISTORTED", f"устаревшая версия пункта ядра {item_id}: опора на переигранную реплику", item_id)

    # Предложения не-людей (ИИ-ассистента) не становятся решениями
    non_human = {normalize(label) for label in core.get("non_human_speakers") or []}
    for finding in findings:
        if claims_agreement(finding) and _non_human(transcript, finding, non_human):
            add_extra(finding, "DISTORTED", "опора — реплика ИИ-ассистента: его предложение не решение людей без явного принятия", None)

    # Находки без опоры в источнике и реестровые записи вне ядра
    for finding in findings:
        if finding.uid in assigned_uids or finding.uid in extras:
            continue
        if finding.quotes and finding.support == "none":
            add_extra(finding, "DISTORTED", "цитаты нет в источнике: находка без опоры", None)
        elif finding.source in ("commitment", "risk") or (finding.source == "decision_area" and claims_agreement(finding)):
            if any(finding.uid in [entry["uid"] for entry in result["findings"]] for result in results):
                continue
            add_extra(finding, "NOT_IN_CORE", "не входит в ядро — проверить, нужен ли этот пункт", None)

    core_results = [result for result in results if result["level"] == "core"]
    supporting = [result for result in results if result["level"] == "supporting"]
    extra_list = sorted(extras.values(), key=lambda entry: (entry["status"] != "DISTORTED", entry["uid"]))
    distorted_extras = [entry for entry in extra_list if entry["status"] == "DISTORTED"]
    passed = all(result["status"] == "FULL" for result in core_results) and not distorted_extras

    def counts(entries: list[dict[str, Any]]) -> dict[str, int]:
        return {status: sum(1 for entry in entries if entry["status"] == status) for status in STATUSES}

    review = [
        {"id": result["id"], "status": result["status"], "level": result["level"], "title": result["title"], "reasons": result["reasons"], "expected_anchor": result["expected_anchor"], "findings": [{"uid": entry["uid"], "quotes": entry["quotes"]} for entry in result["findings"]]}
        for result in results
        if result["status"] != "FULL" and result["level"] == "core"
    ] + [
        {"id": entry["uid"], "status": entry["status"], "level": "extra", "title": entry["summary"], "reasons": entry["reasons"], "expected_anchor": None, "findings": [{"uid": entry["uid"], "quotes": entry["quotes"]}]}
        for entry in distorted_extras
    ]
    return {
        "verdict": "PASS" if passed else "FAIL",
        "valid": passed,
        "summary": {
            "core_items": len(core_results),
            "core": counts(core_results),
            "supporting_items": len(supporting),
            "supporting": counts(supporting),
            "extras_distorted": len(distorted_extras),
            "extras_not_in_core": sum(1 for entry in extra_list if entry["status"] == "NOT_IN_CORE"),
        },
        "items": results,
        "extras": extra_list,
        "human_review": review,
        "findings_total": len(findings),
    }


# ---------------------------------------------------------------------------
# Чтение встречи, запись результата
# ---------------------------------------------------------------------------


def read_artifacts(meeting_path: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    artifacts: dict[str, Any] = {}
    warnings: list[str] = []
    bundle: dict[str, Any] = {}
    bundle_path = meeting_path / "analysis_bundle.json"
    if bundle_path.is_file():
        try:
            loaded = json.loads(bundle_path.read_text(encoding="utf-8-sig"))
            bundle = loaded.get("files") if isinstance(loaded, dict) and isinstance(loaded.get("files"), dict) else {}
        except json.JSONDecodeError:
            warnings.append("analysis_bundle.json: некорректный JSON — пропущен")
    for name in ARTIFACTS:
        path = meeting_path / "analysis" / name
        if path.is_file():
            try:
                artifacts[name] = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                warnings.append(f"{name}: некорректный JSON ({exc.msg}) — считается отсутствующим")
            continue
        packed = bundle.get(f"analysis/{name}")
        if isinstance(packed, dict):
            artifacts[name] = packed
            warnings.append(f"{name}: прочитан из analysis_bundle.json")
    missing = [name for name in ARTIFACTS if name not in artifacts]
    return artifacts, missing, warnings


def _declared_profile(meeting_path: Path) -> str | None:
    """Профиль разбора из review.yaml (analysis_profile: light | {mode: light}); без зависимости от модуля профилей."""
    try:
        review = read_yaml(meeting_path / "review.yaml")
    except NaError:
        return None
    raw = review.get("analysis_profile")
    if isinstance(raw, dict):
        raw = raw.get("mode")
    return raw.strip() if isinstance(raw, str) else None


def _resolve_core_path(root: Path, core_path: Path) -> Path:
    path = Path(core_path).expanduser()
    if path.is_file():
        return path.resolve()
    if not path.is_absolute() and (root / path).is_file():
        return (root / path).resolve()
    raise NaError(f"Файл ядра не найден: {core_path}")


def run_eval(root: Path, meeting_id: str, core_path: Path) -> dict[str, Any]:
    meeting_path = meeting_dir(root, meeting_id)
    core_file = _resolve_core_path(root, core_path)
    core = load_core(core_file)
    transcript_path = meeting_path / "transcript.txt"
    if not transcript_path.is_file():
        raise NaError(f"Нет transcript.txt во встрече {meeting_id}: сверка с источником невозможна")
    transcript_text = transcript_path.read_text(encoding="utf-8-sig")
    anchor_errors = [error for error in check_core(core, transcript_text) if "source_sha256" not in error]
    if anchor_errors:
        raise NaError("Ядро не подходит к транскрипту встречи: " + "; ".join(anchor_errors[:8]))
    artifacts, missing, warnings = read_artifacts(meeting_path)
    if not artifacts:
        raise NaError(f"Во встрече {meeting_id} нет артефактов разбора ({', '.join(ARTIFACTS)}): оценивать нечего")
    transcript_sha = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
    if core.get("source_sha256") and core["source_sha256"] != transcript_sha:
        warnings.append("транскрипт встречи отличается от того, для которого зафиксировано ядро (source_sha256); опорные цитаты при этом найдены")
    for name in missing:
        warnings.append(f"{name} отсутствует — пункты, которые ищутся в нём, получат MISSING")
    profile = _declared_profile(meeting_path)
    if profile == "light":
        kinds = sorted({item["kind"] for item in core["items"] if isinstance(item, dict) and item.get("level", "core") == "core" and item["kind"] in ("authority", "concession")})
        absent = [name for name in ("05_power.json", "09_decision_authority.json") if name in missing]
        if kinds and absent:
            warnings.append(f"встреча разобрана в лёгком профиле (01, 07, 08): пункты ядра видов {', '.join(kinds)} ищутся в 05/09, а {', '.join(absent)} нет — они получат MISSING, лёгкий разбор их не покрывает")
    outcome = evaluate(core, transcript_text, artifacts)
    try:
        shown_core = core_file.relative_to(root.resolve()).as_posix()
    except ValueError:
        shown_core = core_file.name  # без локального пути пользователя в данных встречи
    result = {
        "eval_version": EVAL_VERSION,
        "meeting_id": meeting_id,
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "core": {"path": shown_core, "scenario": core.get("scenario"), "title": core.get("title"), "fixed_at": core.get("fixed_at"), "sha256": hashlib.sha256(core_file.read_bytes()).hexdigest()},
        "transcript_sha256": transcript_sha,
        "source_matches_core": (core.get("source_sha256") == transcript_sha) if core.get("source_sha256") else None,
        "artifacts_read": sorted(artifacts),
        "artifacts_missing": missing,
        "rule": "PASS — все пункты ядра уровня core FULL и среди лишних находок нет DISTORTED; общий процент не считается",
        **outcome,
        "warnings": warnings,
        "outputs": [f"negotiations/{meeting_id}/eval_result.json", f"negotiations/{meeting_id}/eval_result.md"],
    }
    write_json(meeting_path / "eval_result.json", result)
    write_text(meeting_path / "eval_result.md", render_markdown(result))
    return result


# ---------------------------------------------------------------------------
# Представление
# ---------------------------------------------------------------------------

_STATUS_RU = {"FULL": "FULL", "PARTIAL": "PARTIAL", "MISSING": "MISSING", "DISTORTED": "DISTORTED", "NOT_IN_CORE": "вне ядра"}
_KIND_RU = {
    "commitment": "обязательство", "risk": "риск", "concession": "уступка", "agreed_term": "принятое условие",
    "authority": "ЛПР и полномочия", "not_decision": "не решение", "open_question": "открытый вопрос",
    "participant": "участник", "outcome": "исход",
}


def _cell(text: Any) -> str:
    return str(text or "—").replace("|", "\\|").replace("\n", " ")


def _quote_ref(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "—"
    quote = str(entry.get("quote") or "")
    if len(quote) > 110:
        quote = quote[:107] + "…"
    timestamp = entry.get("timestamp")
    return f"«{quote}»" + (f" [{timestamp}]" if timestamp else "")


def render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    core_counts = summary["core"]
    lines = [
        f"# Оценка разбора: {result['meeting_id']}",
        "",
        f"**Вердикт: {result['verdict']}** · ядро: {result['core'].get('scenario') or '—'} (`{result['core']['path']}`) · {result['evaluated_at']}",
        "",
        f"Пункты ядра ({summary['core_items']}): FULL {core_counts['FULL']} · PARTIAL {core_counts['PARTIAL']} · MISSING {core_counts['MISSING']} · DISTORTED {core_counts['DISTORTED']}. "
        f"Лишние находки, противоречащие ядру: {summary['extras_distorted']}; вне ядра: {summary['extras_not_in_core']}.",
        "",
        "Правило: PASS — только если все пункты ядра FULL и среди лишних находок нет DISTORTED. Общий процент не считается.",
        "",
        "## Пункты ядра",
        "",
        "| ID | Вид | Пункт | Статус | Почему | Находка |",
        "|---|---|---|---|---|---|",
    ]
    for item in result["items"]:
        if item["level"] != "core":
            continue
        found = "; ".join(entry["uid"] for entry in item["findings"]) or "—"
        lines.append(f"| {item['id']} | {_KIND_RU.get(item['kind'], item['kind'])} | {_cell(item['title'])} | **{item['status']}** | {_cell('; '.join(item['reasons']))} | {_cell(found)} |")
    lines += ["", "## Лишние находки", ""]
    if result["extras"]:
        lines += ["| Находка | Статус | Причина | Пункт ядра |", "|---|---|---|---|"]
        for entry in result["extras"]:
            lines.append(f"| {_cell(entry['uid'])}: {_cell(entry['summary'])} | **{_STATUS_RU.get(entry['status'], entry['status'])}** | {_cell('; '.join(entry['reasons']))} | {_cell(', '.join(entry['related_items']))} |")
    else:
        lines.append("Нет.")
    supporting = [item for item in result["items"] if item["level"] == "supporting"]
    if supporting:
        lines += ["", "## Поддерживающие пункты (на вердикт не влияют)", "", "| ID | Вид | Пункт | Статус | Почему |", "|---|---|---|---|---|"]
        for item in supporting:
            lines.append(f"| {item['id']} | {_KIND_RU.get(item['kind'], item['kind'])} | {_cell(item['title'])} | {item['status']} | {_cell('; '.join(item['reasons']))} |")
    lines += ["", "## Что посмотреть человеку", ""]
    if result["human_review"]:
        lines.append("Автоматический вердикт не правится. Пересмотр записывается рядом: пункт, новый статус, причина.")
        lines.append("")
        for entry in result["human_review"]:
            lines.append(f"- **{entry['id']}** {entry['status']}: {_cell(entry['title'])}")
            lines.append(f"  - почему: {'; '.join(entry['reasons'])}")
            if entry["expected_anchor"]:
                lines.append(f"  - источник: {_quote_ref(entry['expected_anchor'])}")
            for finding in entry["findings"]:
                for quote in finding["quotes"][:2]:
                    lines.append(f"  - в разборе ({finding['uid']}): {_quote_ref(quote)}; в источнике: {quote.get('at') or 'не найдено'}")
    else:
        lines.append("Нечего: все пункты ядра FULL, искажений нет.")
    lines += [
        "",
        "## Данные",
        "",
        f"- Прочитаны: {', '.join(result['artifacts_read']) or '—'}; отсутствуют: {', '.join(result['artifacts_missing']) or '—'}.",
        f"- Транскрипт совпадает с тем, для которого зафиксировано ядро: {({True: 'да', False: 'нет', None: 'не проверялось'})[result['source_matches_core']]}.",
        f"- Находок прочитано: {result['findings_total']}. Сопоставление детерминированное, без LLM (docs/EVALS.md).",
    ]
    for warning in result["warnings"]:
        lines.append(f"- Предупреждение: {warning}")
    return "\n".join(lines) + "\n"


def summary_lines(result: dict[str, Any]) -> list[str]:
    counts = result["summary"]["core"]
    lines = [
        f"Оценка {result['meeting_id']}: {result['verdict']} — ядро {result['summary']['core_items']}: FULL {counts['FULL']}, PARTIAL {counts['PARTIAL']}, MISSING {counts['MISSING']}, DISTORTED {counts['DISTORTED']}; лишних с искажением: {result['summary']['extras_distorted']}.",
    ]
    for item in result["items"]:
        if item["level"] == "core" and item["status"] != "FULL":
            lines.append(f"  {item['id']} {item['status']}: {item['title']} — {'; '.join(item['reasons'])}")
    for entry in result["extras"]:
        if entry["status"] == "DISTORTED":
            lines.append(f"  лишнее {entry['uid']} DISTORTED: {'; '.join(entry['reasons'])}")
    lines.append(f"Итог записан: {', '.join(result['outputs'])}.")
    for warning in result["warnings"]:
        lines.append(f"  предупреждение: {warning}")
    return lines
