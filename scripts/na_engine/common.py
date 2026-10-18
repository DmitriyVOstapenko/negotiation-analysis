"""Общие утилиты движка: пути, JSON/JSONL, подмножество YAML, хеши, время."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = PLUGIN_ROOT / "core" / "contracts"
TEMPLATES_DIR = PLUGIN_ROOT / "templates"
ANALYSIS_CONTRACTS_PATH = CONTRACTS_DIR / "contracts.schema.json"
EVENT_SCHEMA_PATH = CONTRACTS_DIR / "knowledge_event.schema.json"

ANALYSIS_FILES = [
    "01_extraction.json",
    "02_tactics.json",
    "03_manipulations.json",
    "04_arguments.json",
    "05_power.json",
    "06_emotions.json",
    "07_commitments.json",
    "08_risks.json",
    "09_decision_authority.json",
    "10_argument_map.json",
    "11_meeting_effectiveness.json",
]

PROVIDERS = ["manual", "plaud", "wonderscribe", "local_file", "uploaded_transcript", "document"]
MASKING_MODES = ["none", "initials", "pseudonyms", "full"]
SENSITIVITY_LEVELS = ["public", "internal", "confidential", "regulated"]
ROUTES = ["local_only", "local_and_cloud_llm"]

# Пары реестров «открытый → закрытый». Закрытие не удаляет историю: запись
# переносится целиком, а журнал событий хранит оба снимка.
PAIRED_REGISTRIES = {
    "knowledge/commitments/open.jsonl": "knowledge/commitments/closed.jsonl",
    "knowledge/risks/active.jsonl": "knowledge/risks/closed.jsonl",
}
SINGLE_REGISTRIES = {
    "knowledge/decisions/register.jsonl",
    "knowledge/assignments/register.jsonl",
}
TERMINAL_STATUSES = {
    "knowledge/commitments/open.jsonl": {"done", "cancelled"},
    "knowledge/risks/active.jsonl": {"closed", "realized"},
    "knowledge/decisions/register.jsonl": {"confirmed", "rejected", "superseded", "cancelled"},
    "knowledge/assignments/register.jsonl": {"done", "cancelled"},
}
PROTECTED_KNOWLEDGE_FILES = {"knowledge/review_decisions.jsonl", "knowledge/events/ledger.jsonl"}

MANUAL_MARK = "(m)"


class NaError(Exception):
    """Ошибка предусловия или использования: сообщение печатается без трассировки."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Корень данных
# ---------------------------------------------------------------------------


def resolve_root(explicit: str | os.PathLike[str] | None) -> Path:
    """--root, затем NA_WORKSPACE, затем текущий каталог."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("NA_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def meeting_dir(root: Path, meeting_id: str, must_exist: bool = True) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", meeting_id or ""):
        raise NaError(
            "Некорректный идентификатор встречи: допустимы латиница, цифры, точка, дефис и подчёркивание"
        )
    path = root / "negotiations" / meeting_id
    if must_exist and not path.is_dir():
        raise NaError(f"Встреча не найдена: {relative(path, root)}")
    return path


def meeting_dirs(root: Path) -> list[Path]:
    base = root / "negotiations"
    if not base.is_dir():
        return []
    return sorted(path for path in base.iterdir() if path.is_dir() and not path.name.startswith("."))


def relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def ensure_workspace(root: Path) -> None:
    for name in (
        "negotiations",
        "knowledge/people",
        "knowledge/organizations",
        "knowledge/topics",
        "knowledge/tactics_observed",
        "knowledge/commitments",
        "knowledge/risks",
        "knowledge/decisions",
        "knowledge/assignments",
        "knowledge/events",
        "system",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in (
        "knowledge/commitments/open.jsonl",
        "knowledge/commitments/closed.jsonl",
        "knowledge/risks/active.jsonl",
        "knowledge/risks/closed.jsonl",
        "knowledge/decisions/register.jsonl",
        "knowledge/assignments/register.jsonl",
        "knowledge/review_decisions.jsonl",
        "knowledge/events/ledger.jsonl",
    ):
        path = root / name
        if not path.exists():
            path.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Время, хеши, идентификаторы
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_hash(record: dict[str, Any]) -> str:
    return sha256_text(canonical_json(record))


def slug(value: str, limit: int = 50) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return normalized[:limit] or "source"


# ---------------------------------------------------------------------------
# JSON / JSONL
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise NaError(f"Файл не найден: {path}") from None
    except json.JSONDecodeError as exc:
        raise NaError(f"Некорректный JSON {path}: {exc}") from None


def read_json_or_none(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise NaError(f"Некорректный JSONL {path}, строка {number}: {exc.msg}") from None
        if isinstance(record, dict):
            records.append(record)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def walk(value: Any, path: str = "$") -> Iterator[tuple[str, Any, str]]:
    """Обход JSON: (ключ, значение, json-path) для каждого поля словарей."""
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            yield key, item, child
            yield from walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, f"{path}[{index}]")


def walk_dicts(value: Any, path: str = "$") -> Iterator[tuple[dict[str, Any], str]]:
    if isinstance(value, dict):
        yield value, path
        for key, child in value.items():
            yield from walk_dicts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_dicts(child, f"{path}[{index}]")


# ---------------------------------------------------------------------------
# Подмножество YAML (PyYAML недоступен)
# ---------------------------------------------------------------------------
# Поддерживаются: вложенные словари, списки скаляров и списки словарей,
# скаляры (строки в кавычках и без, null/~, true/false, числа), блочные
# строки «|» и «>», пустые [] и {}, простые потоковые списки [a, b].
# Этого достаточно для meta.yaml, review.yaml и profile/*.yaml.

_NUMBER_RE = re.compile(r"^-?(?:\d+|\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def _strip_comment(raw: str) -> str:
    if raw.lstrip().startswith("#"):
        return ""
    in_single = in_double = False
    escaped = False
    for index, char in enumerate(raw):
        if in_double:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_double = False
            continue
        if in_single:
            if char == "'":
                in_single = False
            continue
        if char == '"':
            in_double = True
        elif char == "'":
            in_single = True
        elif char == "#" and (index == 0 or raw[index - 1] in " \t"):
            return raw[:index].rstrip()
    return raw.rstrip()


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text in ("", "null", "~", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text == "[]":
        return []
    if text == "{}":
        return {}
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text[1:-1]
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    if _NUMBER_RE.match(text):
        try:
            return int(text)
        except ValueError:
            return float(text)
    return text


class _Lines:
    def __init__(self, text: str) -> None:
        self.items: list[tuple[int, str]] = []
        for raw in text.splitlines():
            if raw.strip().startswith("---") and raw.strip() == "---":
                continue
            cleaned = _strip_comment(raw.replace("\t", "  "))
            if not cleaned.strip():
                self.items.append((-1, ""))
                continue
            indent = len(cleaned) - len(cleaned.lstrip(" "))
            self.items.append((indent, cleaned.strip()))
        self.pos = 0

    def peek(self) -> tuple[int, str] | None:
        while self.pos < len(self.items) and self.items[self.pos][0] < 0:
            self.pos += 1
        if self.pos >= len(self.items):
            return None
        return self.items[self.pos]

    def take(self) -> tuple[int, str]:
        item = self.peek()
        if item is None:
            raise NaError("Неожиданный конец YAML")
        self.pos += 1
        return item

    def replace_current(self, indent: int, text: str) -> None:
        self.items[self.pos] = (indent, text)


def _split_key(text: str) -> tuple[str, str] | None:
    if text.startswith("- "):
        return None
    match = re.match(r"^([^:\"']+?|\"[^\"]*\"|'[^']*'):(?:\s+(.*))?$", text)
    if not match:
        return None
    key = match.group(1).strip()
    if key.startswith(('"', "'")):
        key = key[1:-1]
    return key, (match.group(2) or "").strip()


def _parse_block_scalar(lines: _Lines, parent_indent: int, folded: bool) -> str:
    collected: list[str] = []
    while True:
        item = lines.peek() if lines.pos < len(lines.items) else None
        if lines.pos < len(lines.items) and lines.items[lines.pos][0] < 0:
            collected.append("")
            lines.pos += 1
            continue
        if item is None or item[0] <= parent_indent:
            break
        collected.append(item[1])
        lines.pos += 1
    while collected and collected[-1] == "":
        collected.pop()
    return (" ".join(collected) if folded else "\n".join(collected)) + ("\n" if collected else "")


def _parse_node(lines: _Lines, indent: int) -> Any:
    item = lines.peek()
    if item is None:
        return None
    if item[1].startswith("- ") or item[1] == "-":
        return _parse_list(lines, item[0])
    return _parse_mapping(lines, item[0])


def _parse_mapping(lines: _Lines, indent: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    while True:
        item = lines.peek()
        if item is None or item[0] < indent:
            break
        if item[0] > indent:
            raise NaError(f"Неожиданный отступ в YAML: {item[1]!r}")
        split = _split_key(item[1])
        if split is None:
            break
        key, value = split
        lines.take()
        if value == "":
            nxt = lines.peek()
            if nxt is not None and (nxt[0] > indent or (nxt[0] == indent and (nxt[1].startswith("- ") or nxt[1] == "-"))):
                result[key] = _parse_node(lines, nxt[0])
            else:
                result[key] = None
        elif value in ("|", ">", "|-", ">-"):
            result[key] = _parse_block_scalar(lines, indent, folded=value.startswith(">"))
            if value.endswith("-"):
                result[key] = result[key].rstrip("\n")
        else:
            result[key] = _parse_scalar(value)
    return result


def _parse_list(lines: _Lines, indent: int) -> list[Any]:
    result: list[Any] = []
    while True:
        item = lines.peek()
        if item is None or item[0] != indent or not (item[1].startswith("- ") or item[1] == "-"):
            break
        body = item[1][2:].strip() if item[1] != "-" else ""
        if body == "":
            lines.take()
            nxt = lines.peek()
            if nxt is not None and nxt[0] > indent:
                result.append(_parse_node(lines, nxt[0]))
            else:
                result.append(None)
            continue
        if _split_key(body) is not None:
            lines.replace_current(indent + 2, body)
            result.append(_parse_mapping(lines, indent + 2))
            continue
        lines.take()
        result.append(_parse_scalar(body))
    return result


def yaml_load(text: str) -> Any:
    lines = _Lines(text)
    if lines.peek() is None:
        return {}
    value = _parse_node(lines, 0)
    rest = lines.peek()
    if rest is not None:
        raise NaError(f"Не удалось разобрать YAML начиная с: {rest[1]!r}")
    return value


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml_load(read_text(path))
    return value if isinstance(value, dict) else {}


def _yaml_scalar_repr(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_dump_lines(value: Any, indent: int, out: list[str]) -> None:
    pad = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = key if re.fullmatch(r"[A-Za-z0-9_.-]+", str(key)) else json.dumps(str(key), ensure_ascii=False)
            if isinstance(item, dict):
                if not item:
                    out.append(f"{pad}{key_text}: {{}}")
                else:
                    out.append(f"{pad}{key_text}:")
                    _yaml_dump_lines(item, indent + 2, out)
            elif isinstance(item, list):
                if not item:
                    out.append(f"{pad}{key_text}: []")
                else:
                    out.append(f"{pad}{key_text}:")
                    _yaml_dump_lines(item, indent + 2, out)
            elif isinstance(item, str) and "\n" in item:
                out.append(f"{pad}{key_text}: |")
                for line in item.rstrip("\n").split("\n"):
                    out.append(f"{pad}  {line}" if line else "")
            else:
                out.append(f"{pad}{key_text}: {_yaml_scalar_repr(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                if not item:
                    out.append(f"{pad}- {{}}")
                    continue
                nested: list[str] = []
                _yaml_dump_lines(item, indent + 2, nested)
                first = nested[0].strip()
                out.append(f"{pad}- {first}")
                out.extend(nested[1:])
            elif isinstance(item, list):
                out.append(f"{pad}-")
                _yaml_dump_lines(item, indent + 2, out)
            else:
                out.append(f"{pad}- {_yaml_scalar_repr(item)}")
    else:
        out.append(f"{pad}{_yaml_scalar_repr(value)}")


def yaml_dump(value: Any, header: str | None = None) -> str:
    out: list[str] = []
    if header:
        out.extend(f"# {line}" if line else "#" for line in header.splitlines())
    _yaml_dump_lines(value, 0, out)
    return "\n".join(out) + "\n"


def write_yaml(path: Path, value: Any, header: str | None = None) -> None:
    write_text(path, yaml_dump(value, header))


def yaml_scalar(text: str, key: str) -> str | None:
    """Быстрое чтение одного скаляра по первому вхождению ключа (для устаревших файлов)."""
    match = re.search(rf"^\s*{re.escape(key)}:\s*(?:\"([^\"]*)\"|'([^']*)'|([^#\n]+))", text, re.MULTILINE)
    if not match:
        return None
    value = next((part.strip() for part in match.groups() if part is not None), None)
    if value in {None, "", "null", "~"}:
        return None
    return value


def dig(value: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


# ---------------------------------------------------------------------------
# Чтение метаданных встречи с учётом старых форматов
# ---------------------------------------------------------------------------


def meeting_meta(meeting_path: Path) -> dict[str, Any]:
    """Вернуть плоский словарь title/date/sensitivity/… из meta.yaml любого из известных форматов."""
    text = read_text(meeting_path / "meta.yaml")
    try:
        data = yaml_load(text) if text.strip() else {}
    except NaError:
        data = {}
    meeting = data.get("meeting") if isinstance(data, dict) and isinstance(data.get("meeting"), dict) else data
    if not isinstance(meeting, dict):
        meeting = {}
    source = data.get("source") if isinstance(data, dict) and isinstance(data.get("source"), dict) else {}
    date = meeting.get("date") or meeting.get("datetime") or source.get("captured_at") or source.get("captured_at_or_document_date")
    result = {
        "id": meeting.get("id") or meeting_path.name,
        "title": meeting.get("title") or yaml_scalar(text, "title") or meeting_path.name,
        "date": str(date) if date else None,
        "duration_min": meeting.get("duration_min"),
        "meeting_type": meeting.get("meeting_type") or meeting.get("situation_type"),
        "sensitivity": meeting.get("sensitivity") or yaml_scalar(text, "sensitivity"),
        "outcome": meeting.get("outcome"),
        "source": source,
    }
    return result


def gate_state(meeting_path: Path) -> dict[str, Any]:
    """Состояние шлюза согласия с поддержкой устаревшего review.yaml (personal_data.processing_consent)."""
    text = read_text(meeting_path / "review.yaml")
    try:
        review = yaml_load(text) if text.strip() else {}
    except NaError:
        review = {}
    if not isinstance(review, dict):
        review = {}
    gate = review.get("processing_gate") if isinstance(review.get("processing_gate"), dict) else {}
    consent = gate.get("consent")
    legacy = review.get("personal_data") if isinstance(review.get("personal_data"), dict) else {}
    if consent in (None, "not_set") and legacy.get("processing_consent") is True:
        consent = "yes"
    if consent in (None, "not_set") and gate.get("lawful_processing_confirmed") is True:
        consent = "yes"
    return {
        "consent": consent or "not_set",
        "masking_mode": gate.get("masking_mode") or ("none" if legacy.get("masking_required") is False else "not_set"),
        "sensitivity": gate.get("sensitivity") or gate.get("confidentiality") or "not_set",
        "allowed_routes": gate.get("allowed_routes") or gate.get("allowed_route") or "not_set",
        "retention_decision": gate.get("retention_decision") or "not_set",
        "review_status": review.get("review_status"),
        "review": review,
    }


def consent_given(meeting_path: Path) -> bool:
    return gate_state(meeting_path)["consent"] == "yes"
