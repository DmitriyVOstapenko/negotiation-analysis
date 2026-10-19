"""Полный учёт объектов разбора в предложении по базе и решения пользователя по пунктам.

Учёт — раздел ``accounting`` в ``knowledge_proposal.json``. Каждый объект разбора
получает в нём ровно одну судьбу:

- участник и организация (01), обязательство и открытый вопрос (07), риск (08),
  решение с ЛПР, наблюдение о полномочиях и нерешённый пункт карты решений (09) —
  учёт обязателен; темы и ключевые факты (01) — по желанию;
- ``fate``: ``record`` — записываем (цель обязана быть в предложении),
  ``merge`` — объединяем с записью, которая уже есть в базе или в предложении,
  ``skip`` — не переносим; для ``merge`` и ``skip`` обязательна причина;
- ``certainty``: ``sure`` — применяется общим подтверждением, ``doubtful`` — только
  после решения пользователя по этому пункту.

Решения пользователя — ``negotiations/<id>/knowledge_decisions.jsonl`` (только
дописывание): accept / reject / rewrite / merge. Действует последнее решение по пункту
для текущей версии предложения (по SHA-256 файла предложения). Файл предложения решения
не меняют: они накладываются на его копию при каждом diff и apply, поэтому diff всегда
показывает ровно то, что будет записано.

Связка с diff: хеш действующих решений и хеши файлов разбора пишутся в
``knowledge_diff_snapshot.json``; apply сверяет их и отказывает, если после показа diff
решения или разбор изменились, и если по сомнительному пункту нет решения.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .common import (
    NaError,
    append_jsonl,
    canonical_json,
    meeting_dir,
    now_iso,
    read_json_or_none,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from .analysis_profile import meeting_profile, required_analysis_files
from .knowledge import RECEIPT_FILE, _current_file, _safe_relative, _summary, load_proposal, plan_changes


DECISIONS_FILE = "knowledge_decisions.jsonl"
FATES = {"record": "Записываем", "merge": "Объединяем", "skip": "Не переносим"}
CERTAINTIES = {"sure": "уверенный", "doubtful": "сомнительный"}
DECISIONS = {"accept": "принять", "reject": "отклонить", "rewrite": "переписать", "merge": "объединить"}
ACCOUNTING_INPUTS = ("01_extraction.json", "07_commitments.json", "08_risks.json", "09_decision_authority.json")

# вид: (файл разбора, список в нём, поле-идентификатор или None — порядковый номер с 1, учёт обязателен, название)
KINDS: dict[str, tuple[str, str, str | None, bool, str]] = {
    "participant": ("01_extraction.json", "participants", "person_id", True, "Участник"),
    "organization": ("01_extraction.json", "companies", "id", True, "Организация"),
    "topic": ("01_extraction.json", "topics", "id", False, "Тема"),
    "fact": ("01_extraction.json", "key_facts", None, False, "Ключевой факт"),
    "commitment": ("07_commitments.json", "commitments", "id", True, "Обязательство"),
    "question": ("07_commitments.json", "open_questions", None, True, "Открытый вопрос"),
    "risk": ("08_risks.json", "risks", "id", True, "Риск"),
    "decision": ("09_decision_authority.json", "decision_areas", None, True, "Решение и ЛПР"),
    "authority": ("09_decision_authority.json", "authority_observations", None, True, "Полномочия (ЛПР)"),
    "unresolved": ("09_decision_authority.json", "unresolved", None, True, "Нерешённое в карте решений"),
}

# Поле с формулировкой записи — его заменяет решение «переписать»
MAIN_TEXT_FIELDS = {
    "knowledge/commitments/open.jsonl": "what",
    "knowledge/commitments/closed.jsonl": "what",
    "knowledge/risks/active.jsonl": "description",
    "knowledge/risks/closed.jsonl": "description",
    "knowledge/decisions/register.jsonl": "statement",
    "knowledge/assignments/register.jsonl": "statement",
}
TEXT_FALLBACK = ("what", "description", "statement", "title", "decision")


# ---------------------------------------------------------------------------
# Объекты разбора
# ---------------------------------------------------------------------------


def _short(value: Any, limit: int = 110) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _label(kind: str, node: Any) -> str:
    if isinstance(node, str):
        return _short(node)
    if not isinstance(node, dict):
        return "—"
    if kind == "participant":
        role = node.get("role_freeform") or node.get("role")
        company = node.get("company_id")
        return _short(f"{node.get('full_name') or node.get('person_id')}" + (f" ({role})" if role else "") + (f", {company}" if company else ""))
    if kind == "organization":
        short = node.get("short_name") or node.get("id")
        legal = node.get("legal_name")
        return _short(f"{short}" + (f" ({legal})" if legal and legal != short else ""))
    if kind == "topic":
        return _short(node.get("title") or node.get("id"))
    if kind == "fact":
        return _short(node.get("fact"))
    if kind == "commitment":
        deadline = f"срок {node['deadline']}" if node.get("deadline") else "срок не назван"
        return f"{node.get('who')}: {_short(node.get('what'), 100)} ({deadline})"
    if kind == "question":
        return f"{_short(node.get('question'), 100)} (тема {node.get('topic')})"
    if kind == "risk":
        return f"{_short(node.get('description'), 100)} (важность {node.get('severity')})"
    if kind == "decision":
        owner = node.get("final_decision_maker") or "не назван"
        return f"{_short(node.get('decision'), 70)} — {_short(node.get('current_state'), 60)}; ЛПР: {owner}"
    if kind == "authority":
        return _short(f"{node.get('person_id')}: {node.get('observed_role')}")
    return "—"


def analysis_objects(meeting_path: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Объекты разбора, подлежащие учёту: (объекты, ошибки, предупреждения).

    Учёт обязателен только для артефактов, которые строит профиль разбора встречи
    (full — 01/07/08/09, light — 01/07/08). Артефакт вне профиля, если он всё же есть,
    можно учитывать по желанию; его отсутствие — не предупреждение.
    """
    analysis = meeting_path / "analysis"
    profile = meeting_profile(meeting_path)
    expected = [name for name in ACCOUNTING_INPUTS if name in set(required_analysis_files(meeting_path))]
    objects: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    present = [name for name in ACCOUNTING_INPUTS if (analysis / name).is_file()]
    if not present:
        warnings.append(f"В каталоге встречи нет файлов разбора {'/'.join(name[:2] for name in expected)}: полнота учёта не проверяется, сверяются только ссылки пунктов.")
    else:
        warnings += [f"Нет analysis/{name}: его объекты не проверены на полноту учёта." for name in expected if name not in present]
    loaded: dict[str, Any] = {}
    seen: set[str] = set()
    for kind, (file_name, list_key, id_key, required, title) in KINDS.items():
        if file_name not in loaded:
            loaded[file_name] = read_json_or_none(analysis / file_name)
            if file_name in present and not isinstance(loaded[file_name], dict):
                errors.append(f"учёт: analysis/{file_name} не читается как JSON-объект — объекты разбора не определить")
        data = loaded[file_name]
        if not isinstance(data, dict) or not isinstance(data.get(list_key), list):
            continue
        in_profile = file_name in expected
        for position, node in enumerate(data[list_key], start=1):
            key = str(node[id_key]) if id_key and isinstance(node, dict) and node.get(id_key) else str(position)
            ref = f"{kind}:{key}"
            if ref in seen:
                errors.append(f"учёт: в разборе повторяется объект {ref} (analysis/{file_name}) — ссылка неоднозначна, исправьте разбор")
                continue
            seen.add(ref)
            objects.append({"ref": ref, "kind": kind, "title": title, "label": _label(kind, node), "required": required and in_profile, "source": f"analysis/{file_name}", "json_path": f"$.{list_key}[{position - 1}]", "profile": profile})
    return objects, errors, warnings


def analysis_input_hashes(meeting_path: Path) -> dict[str, str | None]:
    return {f"analysis/{name}": sha256_file(meeting_path / "analysis" / name) for name in ACCOUNTING_INPUTS}


# ---------------------------------------------------------------------------
# Цели и фрагменты
# ---------------------------------------------------------------------------


def parse_target(raw: Any) -> dict[str, str]:
    """{"registry", "id"} или {"file"[, "fragment"][, "section"]}; строки «реестр.jsonl#id», «файл.md[#Раздел]»."""
    if isinstance(raw, str):
        base, _, tail = raw.strip().partition("#")
        if base.endswith(".jsonl"):
            raw = {"registry": base, "id": tail.strip()}
        elif base.endswith(".md"):
            raw = {"file": base, **({"section": tail.strip()} if tail.strip() else {})}
        else:
            raise NaError(f"цель {raw!r}: ожидается knowledge/….jsonl#<id> или knowledge/….md[#Раздел]")
    if not isinstance(raw, dict):
        raise NaError("цель должна быть объектом {registry, id} или {file[, fragment]}")
    if raw.get("registry"):
        registry = _safe_relative(raw["registry"])
        if not registry.endswith(".jsonl"):
            raise NaError(f"цель {registry}: реестр должен быть JSONL")
        if not raw.get("id"):
            raise NaError(f"цель {registry}: не указан id записи")
        return {"registry": registry, "id": str(raw["id"])}
    if raw.get("file"):
        path = _safe_relative(raw["file"])
        if not path.endswith(".md"):
            raise NaError(f"цель {path}: карточка должна быть Markdown-файлом")
        target = {"file": path}
        for key in ("fragment", "section"):
            if raw.get(key) is not None:
                if not isinstance(raw[key], str) or not raw[key].strip():
                    raise NaError(f"цель {path}: {key} должен быть непустой строкой")
                target[key] = raw[key] if key == "fragment" else raw[key].strip()
        return target
    raise NaError("цель: укажите registry и id записи или file карточки")


def target_text(target: dict[str, str] | None) -> str:
    if not target:
        return "—"
    if "registry" in target:
        return f"`{target['registry']}` · `{target['id']}`"
    text = f"`{target['file']}`"
    if target.get("section"):
        text += f", раздел «{target['section']}»"
    if target.get("fragment"):
        text += f", фрагмент «{_short(target['fragment'], 70)}»"
    return text


def _target_key(target: dict[str, str]) -> tuple[str, ...]:
    if "registry" in target:
        return ("rec", target["registry"], target["id"])
    if target.get("fragment"):
        return ("frag", target["file"], target["fragment"])
    return ("file", target["file"])


def _fragment_problem(content: str, fragment: str) -> str | None:
    count = content.count(fragment)
    if count == 0:
        return "фрагмент не найден в содержимом карточки"
    if count > 1:
        return f"фрагмент встречается {count} раза — нужен уникальный"
    start = content.index(fragment)
    end = start + len(fragment)
    if start and content[start - 1] != "\n":
        return "фрагмент должен начинаться с начала строки (только целые строки)"
    if not (fragment.endswith("\n") or end == len(content) or content[end] == "\n"):
        return "фрагмент должен заканчиваться концом строки (только целые строки)"
    return None


def _remove_fragment(content: str, fragment: str) -> str | None:
    if _fragment_problem(content, fragment):
        return None
    start = content.index(fragment)
    end = start + len(fragment)
    if not fragment.endswith("\n") and content[end:end + 1] == "\n":
        end += 1
    return content[:start] + content[end:]


def _insert_block(content: str, block: str, section: str | None) -> str | None:
    """Вставить строки в конец раздела, иначе перед «## Ручные заметки», иначе в конец файла."""
    block = block if block.endswith("\n") else block + "\n"
    lines = content.splitlines(keepends=True)
    if section:
        start = next((index for index, line in enumerate(lines) if line.startswith("#") and line.lstrip("#").strip() == section), None)
        if start is None:
            return None
        level = len(lines[start]) - len(lines[start].lstrip("#"))
        end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("#") and len(lines[index]) - len(lines[index].lstrip("#")) <= level), len(lines))
        position = end
        while position > start + 1 and not lines[position - 1].strip():
            position -= 1
        if position == start + 1:
            block = "\n" + block
    else:
        manual = next((index for index, line in enumerate(lines) if line.strip() == "## Ручные заметки"), None)
        position = len(lines) if manual is None else manual
        while manual is not None and position > 0 and not lines[position - 1].strip():
            position -= 1
    if position > 0 and not lines[position - 1].endswith("\n"):
        lines[position - 1] += "\n"
    if position > 0 and lines[position - 1].lstrip().startswith("|") and not block.startswith("\n"):
        block = "\n" + block  # строка после таблицы — отдельным блоком, иначе Markdown склеит её с таблицей
    lines.insert(position, block)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Наложение решений на копию предложения
# ---------------------------------------------------------------------------


def _entry_id(entry: dict[str, Any]) -> str:
    record = entry.get("record") if isinstance(entry.get("record"), dict) else {}
    return str(entry.get("id") or record.get("id") or "")


def _norm(raw: Any) -> str | None:
    try:
        return _safe_relative(raw or "")
    except NaError:
        return None


class _Overlay:
    """Копия предложения, к которой применяются решения пользователя."""

    def __init__(self, root: Path, proposal: dict[str, Any]) -> None:
        self.root = root
        self.proposal = copy.deepcopy(proposal)
        self.proposal["files"] = list(self.proposal.get("files") or [])
        self.proposal["records"] = list(self.proposal.get("records") or [])
        self.dropped: list[tuple[str, str]] = []

    def file_entry(self, path: str) -> dict[str, Any] | None:
        return next((entry for entry in self.proposal["files"] if isinstance(entry, dict) and _norm(entry.get("path")) == path), None)

    def record_entries(self, registry: str, record_id: str) -> list[dict[str, Any]]:
        return [entry for entry in self.proposal["records"] if isinstance(entry, dict) and _norm(entry.get("registry")) == registry and _entry_id(entry) == record_id]

    def base_record(self, registry: str, record_id: str) -> dict[str, Any] | None:
        return next((record for record in read_jsonl(self.root / registry) if str(record.get("id")) == record_id), None)

    def effective_record(self, registry: str, record_id: str) -> dict[str, Any] | None:
        base = self.base_record(registry, record_id)
        current = dict(base) if base else None
        for entry in self.record_entries(registry, record_id):
            record = entry.get("record") if isinstance(entry.get("record"), dict) else {}
            if entry.get("action") == "append":
                current = dict(record, id=record_id)
            elif current is not None:
                current = {**current, **record, "id": record_id}
        return current

    def content(self, path: str) -> tuple[str | None, dict[str, Any] | None]:
        entry = self.file_entry(path)
        if entry is not None and isinstance(entry.get("content"), str):
            return entry["content"], entry
        current_path, text = _current_file(self.root, path)
        return (text if current_path is not None else None), None

    def set_content(self, path: str, text: str) -> None:
        entry = self.file_entry(path)
        if entry is not None:
            entry["content"] = text
        else:
            self.proposal["files"].append({"path": path, "action": "update", "content": text})

    def removes_something(self, target: dict[str, str]) -> bool:
        if "registry" in target:
            return bool(self.record_entries(target["registry"], target["id"]))
        return self.file_entry(target["file"]) is not None

    # --- операции -----------------------------------------------------------

    def drop(self, target: dict[str, str]) -> str | None:
        if "registry" in target:
            doomed = {id(entry) for entry in self.record_entries(target["registry"], target["id"])}
            if doomed:
                self.proposal["records"] = [entry for entry in self.proposal["records"] if id(entry) not in doomed]
                self.dropped.append((target["registry"], target["id"]))
            return None
        entry = self.file_entry(target["file"])
        if entry is None:
            return None  # предложение эту карточку не меняет — отклонять нечего
        if not target.get("fragment"):
            self.proposal["files"] = [item for item in self.proposal["files"] if item is not entry]
            return None
        updated = _remove_fragment(entry.get("content") or "", target["fragment"])
        if updated is None:
            return f"фрагмент не удалось убрать из {target['file']}: " + (_fragment_problem(entry.get("content") or "", target["fragment"]) or "неизвестная причина")
        entry["content"] = updated
        return None

    def rewrite(self, target: dict[str, str], text: str) -> str | None:
        if "registry" in target:
            registry, record_id = target["registry"], target["id"]
            effective = self.effective_record(registry, record_id)
            if effective is None:
                return f"запись {record_id} не найдена ни в предложении, ни в {registry}"
            field = MAIN_TEXT_FIELDS.get(registry) or next((key for key in TEXT_FALLBACK if key in effective), None)
            if field is None:
                return "у записи нет поля формулировки (what, description, statement, title)"
            entries = self.record_entries(registry, record_id)
            if entries:
                last = entries[-1]
                if not isinstance(last.get("record"), dict):
                    last["record"] = {}
                last["record"][field] = text
            else:
                self.proposal["records"].append({"registry": registry, "action": "update", "id": record_id, "record": {field: text}, "change_reason": "новая формулировка по решению пользователя"})
            return None
        fragment = target.get("fragment")
        if not fragment:
            return "переписать можно запись реестра или фрагмент карточки; у пункта цель — карточка целиком (добавьте fragment в учёт или пересоберите предложение)"
        content, _ = self.content(target["file"])
        if content is None:
            return f"карточка {target['file']} не найдена"
        problem = _fragment_problem(content, fragment)
        if problem:
            return f"{target['file']}: {problem}"
        replacement = text if (text.endswith("\n") or not fragment.endswith("\n")) else text + "\n"
        self.set_content(target["file"], content.replace(fragment, replacement, 1))
        return None

    def merge_into(self, source: dict[str, str], dest: dict[str, str], meeting_id: str) -> str | None:
        if "registry" in source:
            return self._merge_record(source, dest, meeting_id)
        fragment = source.get("fragment")
        if not fragment:
            return "карточка целиком не объединяется автоматически: отклоните пункт и пересоберите предложение (или укажите fragment в учёте)"
        if "file" not in dest:
            return "фрагмент карточки объединяется только с карточкой (knowledge/….md[#Раздел])"
        if dest["file"] == source["file"]:
            return "цель объединения совпадает с исходной карточкой"
        source_entry = self.file_entry(source["file"])
        if source_entry is None:
            return "предложение не вносит этот фрагмент — объединять нечего"
        dest_content, _ = self.content(dest["file"])
        if dest_content is None:
            return f"карточка {dest['file']} не найдена ни в базе, ни в предложении"
        removed = _remove_fragment(source_entry.get("content") or "", fragment)
        if removed is None:
            return f"фрагмент не найден ровно один раз целыми строками в {source['file']}"
        inserted = _insert_block(dest_content, fragment, dest.get("section"))
        if inserted is None:
            return f"в {dest['file']} нет раздела «{dest.get('section')}»"
        source_entry["content"] = removed
        self.set_content(dest["file"], inserted)
        return None

    def _merge_record(self, source: dict[str, str], dest: dict[str, str], meeting_id: str) -> str | None:
        registry, record_id = source["registry"], source["id"]
        if "registry" not in dest:
            return "запись реестра объединяется только с записью того же реестра (knowledge/….jsonl#<id> или просто <id>)"
        if dest["registry"] != registry:
            return f"цель объединения в другом реестре ({dest['registry']}); допустим только {registry}"
        if dest["id"] == record_id:
            return "нельзя объединить запись с самой собой"
        entries = self.record_entries(registry, record_id)
        if len(entries) != 1 or entries[0].get("action") != "append":
            return "объединить можно только новую запись этого предложения (action=append)"
        dest_effective = self.effective_record(registry, dest["id"])
        if dest_effective is None:
            return f"запись {dest['id']} не найдена ни в {registry}, ни в предложении"
        dest_entries = self.record_entries(registry, dest["id"])
        if dest_entries and dest_entries[-1].get("action") == "close":
            return f"запись {dest['id']} закрывается этим предложением — объединение с ней не выполняется"
        incoming = dict(entries[0].get("record") if isinstance(entries[0].get("record"), dict) else {}, id=record_id)
        changes: dict[str, Any] = {}
        for key, value in incoming.items():
            existing = dest_effective.get(key)
            if key == "id" or not isinstance(value, list) or not isinstance(existing, list):
                continue
            merged = list(existing)
            known = {canonical_json(item) for item in merged}
            for item in value:
                if canonical_json(item) not in known:
                    merged.append(item)
                    known.add(canonical_json(item))
            if len(merged) != len(existing):
                changes[key] = merged
        if incoming.get("last_seen_meeting_id") and "last_seen_meeting_id" in dest_effective and dest_effective.get("last_seen_meeting_id") != incoming["last_seen_meeting_id"]:
            changes["last_seen_meeting_id"] = incoming["last_seen_meeting_id"]
        provenance: dict[str, Any] = {"id": record_id, "meeting_id": meeting_id, "summary": _summary(incoming), "decided_by": "user_confirmation"}
        if incoming.get("source_quote"):
            provenance["quote"] = incoming["source_quote"]
            provenance["timestamp"] = incoming.get("source_timestamp")
        merged_from = [item for item in (dest_effective.get("merged_from") or []) if isinstance(item, dict)]
        if not any(item.get("id") == record_id for item in merged_from):
            merged_from.append(provenance)
        changes["merged_from"] = merged_from
        self.drop(source)
        if dest_entries:
            last = dest_entries[-1]
            if not isinstance(last.get("record"), dict):
                last["record"] = {}
            last["record"].update(changes)
        else:
            self.proposal["records"].append({"registry": registry, "action": "update", "id": dest["id"], "record": changes, "change_reason": f"объединение с {record_id} по решению пользователя"})
        return None


# ---------------------------------------------------------------------------
# Решения пользователя
# ---------------------------------------------------------------------------


def load_decisions(meeting_path: Path, proposal_sha: str) -> tuple[dict[str, dict[str, Any]], int]:
    """Действующие решения (последнее по пункту) для этой версии предложения и число устаревших строк."""
    effective: dict[str, dict[str, Any]] = {}
    stale = 0
    for line in read_jsonl(meeting_path / DECISIONS_FILE):
        if line.get("proposal_sha256") != proposal_sha:
            stale += 1
            continue
        ref = str(line.get("item") or "").strip()
        if ref:
            effective[ref] = line
    return effective, stale


def decisions_digest(decisions: dict[str, dict[str, Any]]) -> str | None:
    if not decisions:
        return None
    rows = [{"item": ref, "decision": item.get("decision"), "text": item.get("text"), "target": item.get("target")} for ref, item in sorted(decisions.items())]
    return sha256_text(canonical_json(rows))


def decision_text(decision: dict[str, Any] | None) -> str:
    if not decision:
        return "ждёт ответа"
    kind = decision.get("decision")
    text = DECISIONS.get(kind, str(kind))
    if kind == "rewrite":
        text += f": «{_short(decision.get('text'), 90)}»"
    if kind == "merge":
        try:
            text += " с " + target_text(parse_target(decision.get("target")))
        except NaError:
            text += f" с {decision.get('target')!r}"
    return text + f" ({decision.get('source') or 'user_confirmation'}, {decision.get('recorded_at') or '—'})"


def _decision_problem(item: dict[str, Any], decision: dict[str, Any]) -> str | None:
    kind = decision.get("decision")
    fate = item["fate"]
    if kind not in DECISIONS:
        return f"решение {kind!r} неизвестно; допустимо accept, reject, rewrite, merge"
    if kind == "rewrite" and not str(decision.get("text") or "").strip():
        return "для «переписать» нужна новая формулировка (text)"
    if kind == "merge" and not decision.get("target"):
        return "для «объединить» нужна запись-цель (target)"
    if fate == "skip" and kind in ("rewrite", "merge"):
        return "для непереносимого объекта доступно только «принять» (не переносить) или «отклонить» (перенести — предложение пересобирается)"
    if fate == "merge" and kind == "merge":
        return "пункт уже объединяется с записью; чтобы объединить с другой — отклоните пункт и пересоберите предложение"
    return None


# ---------------------------------------------------------------------------
# Проверка учёта и эффективное предложение
# ---------------------------------------------------------------------------


def _check_target(overlay: _Overlay, fate: str, target: dict[str, str]) -> str | None:
    if "registry" in target:
        registry, record_id = target["registry"], target["id"]
        in_proposal = bool(overlay.record_entries(registry, record_id))
        if fate == "record" and not in_proposal:
            return f"record: записи {registry} · {record_id} нет в records предложения"
        if fate == "merge" and not in_proposal and overlay.base_record(registry, record_id) is None:
            return f"merge: запись {record_id} не найдена ни в предложении, ни в {registry}"
        return None
    path = target["file"]
    if fate == "record" and overlay.file_entry(path) is None:
        return f"record: файла {path} нет в files предложения"
    content, _ = overlay.content(path)
    if content is None:
        return f"merge: карточка {path} не найдена ни в предложении, ни в базе"
    if target.get("fragment"):
        problem = _fragment_problem(content, target["fragment"])
        if problem:
            return f"{path}: {problem}"
    return None


def _overlaps(removed: tuple[str, ...], other: tuple[str, ...]) -> bool:
    if removed[0] == "file":
        return other[0] in ("file", "frag") and other[1] == removed[1]
    return other == removed


def evaluate(root: Path, meeting_path: Path, proposal: dict[str, Any], proposal_sha: str, decisions_override: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Проверить учёт, наложить решения и вернуть эффективное предложение с ошибками и предупреждениями."""
    if decisions_override is not None:
        decisions, stale = decisions_override, 0
    else:
        decisions, stale = load_decisions(meeting_path, proposal_sha)
    result: dict[str, Any] = {
        "present": "accounting" in proposal,
        "errors": [],
        "warnings": [],
        "item_errors": {},
        "objects": [],
        "items": [],
        "unaccounted": [],
        "double": [],
        "pending": [],
        "rebuild": [],
        "structural_errors": [],
        "untracked": [],
        "decisions": {},
        "decisions_sha256": None,
        "stale_decisions": stale,
        "analysis_inputs": None,
        "effective_proposal": proposal,
    }
    errors: list[str] = result["errors"]
    warnings: list[str] = result["warnings"]

    def item_error(ref: str, where: str, message: str) -> None:
        errors.append(f"учёт: {where} {ref}: {message}")
        result["item_errors"].setdefault(ref, []).append(message)

    if not result["present"]:
        warnings.append("Учёт не заполнен: в предложении нет раздела accounting, полнота переноса объектов разбора не проверена. Применение не блокируется; новые предложения готовьте с учётом (docs/SCRIPTS.md).")
        if decisions:
            warnings.append(f"Решения по пунктам ({len(decisions)}) не учитываются: в предложении нет раздела учёта.")
        result["summary"] = _counts(result)
        return result

    result["analysis_inputs"] = analysis_input_hashes(meeting_path)
    result["profile"] = meeting_profile(meeting_path)
    raw = proposal.get("accounting")
    if not isinstance(raw, list):
        errors.append("учёт: поле accounting должно быть списком пунктов")
        result["summary"] = _counts(result)
        return result
    objects, object_errors, object_warnings = analysis_objects(meeting_path)
    errors += object_errors
    warnings += object_warnings
    result["objects"] = objects
    by_ref = {item["ref"]: item for item in objects}
    overlay = _Overlay(root, proposal)
    positions: dict[str, list[int]] = {}

    for index, entry in enumerate(raw):
        where = f"accounting[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"учёт: {where}: ожидается объект")
            continue
        ref = str(entry.get("ref") or "").strip()
        if not ref:
            errors.append(f"учёт: {where}: не указан ref объекта разбора")
            continue
        positions.setdefault(ref, []).append(index)
        kind = ref.split(":", 1)[0]
        known = by_ref.get(ref)
        fate = entry.get("fate")
        certainty = entry.get("certainty")
        reason = str(entry.get("reason") or "").strip()
        item = {
            "index": index,
            "ref": ref,
            "kind": kind,
            "title": known["title"] if known else KINDS.get(kind, ("", "", None, False, kind))[4],
            "label": known["label"] if known else "объект не найден в разборе",
            "fate": fate,
            "certainty": certainty,
            "reason": reason or None,
            "question": str(entry.get("question") or "").strip() or None,
            "target": None,
            "decision": None,
            "valid": True,
        }
        problems: list[str] = []
        if kind not in KINDS:
            problems.append(f"неизвестный вид объекта {kind!r}; допустимо: {', '.join(KINDS)}")
        elif known is None:
            problems.append("объект не найден в разборе встречи (проверьте ref)")
        if fate not in FATES:
            problems.append("fate должен быть record, merge или skip")
        if certainty not in CERTAINTIES:
            problems.append("certainty должен быть sure или doubtful")
        if fate in ("merge", "skip") and not reason:
            problems.append(f"для fate={fate} обязательна причина (reason)")
        if fate in ("record", "merge"):
            if entry.get("target") is None:
                problems.append(f"для fate={fate} нужна цель target")
            else:
                try:
                    item["target"] = parse_target(entry["target"])
                except NaError as exc:
                    problems.append(str(exc))
                else:
                    if item["target"].get("section"):
                        problems.append("section задаётся только в решении «объединить», в учёте укажите fragment")
                    else:
                        problem = _check_target(overlay, fate, item["target"])
                        if problem:
                            problems.append(problem)
        for problem in problems:
            item_error(ref, where, problem)
        item["valid"] = not problems
        result["items"].append(item)

    for ref, indexes in positions.items():
        if len(indexes) > 1:
            result["double"].append(ref)
            message = f"двойной учёт — пункты {', '.join(f'accounting[{i}]' for i in indexes)}; у объекта должна быть ровно одна судьба"
            errors.append(f"учёт: {ref}: {message}")
            result["item_errors"].setdefault(ref, []).append(message)
    accounted = set(positions)
    for item in objects:
        if item["required"] and item["ref"] not in accounted:
            result["unaccounted"].append(item["ref"])
            errors.append(f"учёт: не учтён объект разбора {item['ref']} — {item['title']}: {item['label']} ({item['source']}); дайте ему судьбу record, merge или skip")

    result["structural_errors"] = list(errors)  # ошибки самого предложения, до наложения решений

    # Решения пользователя
    first_item = {}
    for item in result["items"]:
        first_item.setdefault(item["ref"], item)
    if stale:
        warnings.append(f"Решений к прежним версиям предложения: {stale} — они не учитываются; по сомнительным пунктам этой версии нужны новые ответы.")
    for ref, decision in decisions.items():
        item = first_item.get(ref)
        if item is None:
            warnings.append(f"Решение по пункту {ref} не учитывается: такого пункта нет в учёте.")
            continue
        item["decision"] = decision
        result["decisions"][ref] = decision
        if item["fate"] in FATES:
            problem = _decision_problem(item, decision)
            if problem:
                item_error(ref, "решение", problem)
                item["valid"] = False
    result["decisions_sha256"] = decisions_digest(result["decisions"])
    result["pending"] = [item["ref"] for item in result["items"] if item["certainty"] == "doubtful" and item["decision"] is None]

    # Отклонение и объединение убирают цель целиком — нельзя, если она общая с другими пунктами
    def removes(item: dict[str, Any]) -> bool:
        kind = (item["decision"] or {}).get("decision")
        return item["valid"] and item["target"] is not None and (kind == "merge" or (kind == "reject" and item["fate"] in ("record", "merge")))

    removing = [item for item in result["items"] if removes(item)]
    for item in removing:
        if not overlay.removes_something(item["target"]):
            continue
        key = _target_key(item["target"])
        shared = [other["ref"] for other in result["items"] if other is not item and other["target"] is not None and not removes(other) and _overlaps(key, _target_key(other["target"]))]
        if shared:
            item_error(item["ref"], "решение", f"цель {target_text(item['target'])} общая с пунктами {', '.join(shared)} — убрать её только для этого пункта нельзя; решите их так же или пересоберите предложение")
            item["valid"] = False

    meeting_id = str(proposal.get("meeting_id") or meeting_path.name)
    for item in result["items"]:
        decision = item["decision"]
        if not decision or not item["valid"] or decision.get("decision") == "accept":
            continue
        kind = decision["decision"]
        problem: str | None = None
        if kind == "reject" and item["fate"] == "skip":
            # ответ пользователя сохраняется, но записать объект может только пересобранное предложение
            result["rebuild"].append(item["ref"])
            errors.append(f"учёт: {item['ref']}: пользователь не согласен не переносить объект — пересоберите предложение с записью этого объекта (новая версия предложения, вопросы заново)")
            continue
        if kind == "reject":
            problem = overlay.drop(item["target"])
        elif kind == "rewrite":
            problem = overlay.rewrite(item["target"], str(decision.get("text")).strip())
        elif kind == "merge":
            try:
                dest = _resolve_decision_target(decision.get("target"), item["target"])
            except NaError as exc:
                problem = str(exc)
            else:
                problem = overlay.merge_into(item["target"], dest, meeting_id)
        if problem:
            item_error(item["ref"], "решение", problem)

    remaining_text = "\n".join(entry["content"] for entry in overlay.proposal["files"] if isinstance(entry, dict) and isinstance(entry.get("content"), str))
    for registry, record_id in overlay.dropped:
        if record_id in remaining_text and overlay.effective_record(registry, record_id) is None:
            warnings.append(f"Запись {record_id} по решению пользователя не создаётся, но упоминается в карточках предложения — поправьте карточки или пересоберите предложение.")

    targets = [item["target"] for item in result["items"] if item["target"]]
    covered_records = {(target["registry"], target["id"]) for target in targets if "registry" in target}
    covered_files = {target["file"] for target in targets if "file" in target}
    for entry in proposal.get("records") or []:
        if isinstance(entry, dict) and (_norm(entry.get("registry")), _entry_id(entry)) not in covered_records:
            result["untracked"].append(f"`{entry.get('registry')}` · `{_entry_id(entry)}` ({entry.get('action')})")
    for entry in proposal.get("files") or []:
        if isinstance(entry, dict) and _norm(entry.get("path")) not in covered_files:
            result["untracked"].append(f"`{entry.get('path')}` ({entry.get('action')})")

    result["effective_proposal"] = overlay.proposal
    result["summary"] = _counts(result)
    return result


def _resolve_decision_target(raw: Any, own: dict[str, str]) -> dict[str, str]:
    """Цель решения «объединить»: полная форма или голый id записи того же реестра."""
    if isinstance(raw, str) and "registry" in own and not raw.strip().split("#", 1)[0].endswith((".jsonl", ".md")):
        return {"registry": own["registry"], "id": raw.strip()}
    return parse_target(raw)


def _counts(result: dict[str, Any]) -> dict[str, Any]:
    items = result["items"]
    doubtful = [item for item in items if item["certainty"] == "doubtful"]
    return {
        "present": result["present"],
        "profile": result.get("profile"),
        "objects": sum(1 for item in result["objects"] if item["required"]),
        "items": len(items),
        "record": sum(1 for item in items if item["fate"] == "record"),
        "merge": sum(1 for item in items if item["fate"] == "merge"),
        "skip": sum(1 for item in items if item["fate"] == "skip"),
        "sure": sum(1 for item in items if item["certainty"] == "sure"),
        "doubtful": len(doubtful),
        "decided": sum(1 for item in doubtful if item["decision"] is not None),
        "pending": list(result["pending"]),
        "unaccounted": list(result["unaccounted"]),
        "rebuild": list(result["rebuild"]),
        "double": list(result["double"]),
        "errors": len(result["errors"]),
        "decisions_sha256": result["decisions_sha256"],
        "stale_decisions": result["stale_decisions"],
    }


# ---------------------------------------------------------------------------
# Связка с diff и apply
# ---------------------------------------------------------------------------


def snapshot_fields(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Поля снимка diff: хеш действующих решений и хеши файлов разбора, по которым сверялся учёт."""
    return {"decisions_sha256": evaluation["decisions_sha256"], "analysis_inputs": evaluation["analysis_inputs"]}


def check_ready_for_apply(evaluation: dict[str, Any], snapshot: dict[str, Any]) -> None:
    """Отказ apply: решения или разбор изменились после показа diff; по сомнительному пункту нет решения."""
    if snapshot.get("decisions_sha256") != evaluation["decisions_sha256"]:
        raise NaError(
            f"Применение остановлено: после показа diff изменились решения по пунктам ({DECISIONS_FILE}). Ничего не записано. "
            "Выполните knowledge diff заново и покажите пользователю, что теперь будет записано, затем apply --confirm."
        )
    expected = snapshot.get("analysis_inputs")
    if evaluation["present"] and isinstance(expected, dict):
        changed = sorted(name for name, digest in expected.items() if (evaluation["analysis_inputs"] or {}).get(name) != digest)
        if changed:
            raise NaError(
                "Применение остановлено: после показа diff изменились файлы разбора — " + ", ".join(changed)
                + ". Ничего не записано. Сверьте учёт, выполните knowledge diff заново и покажите пользователю.",
                exit_code=1,
            )
    if evaluation["pending"]:
        raise NaError(
            "Применение остановлено: нет решения пользователя по сомнительным пунктам — " + ", ".join(evaluation["pending"])
            + ". Задайте вопросы одним блоком, запишите ответы (na.py knowledge decide --meeting … --item … --decision accept|reject|rewrite|merge), "
            "покажите diff заново, затем apply --confirm. Уверенные пункты применяются общим подтверждением, сомнительные — только по решению по каждому."
        )


def receipt_entry(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Что о учёте и решениях сохранить в квитанции knowledge_apply.json."""
    summary = evaluation["summary"]
    return {
        "present": evaluation["present"],
        "objects": summary["objects"],
        "items": summary["items"],
        "record": summary["record"],
        "merge": summary["merge"],
        "skip": summary["skip"],
        "sure": summary["sure"],
        "doubtful": summary["doubtful"],
        "decisions_sha256": evaluation["decisions_sha256"],
        "decisions": [
            {key: decision.get(key) for key in ("decision_id", "item", "decision", "text", "target", "source", "recorded_at")}
            for _, decision in sorted(evaluation["decisions"].items())
        ],
    }


# ---------------------------------------------------------------------------
# Раздел «Учёт» в knowledge_diff.md
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _proposed(item: dict[str, Any]) -> str:
    if item["fate"] == "record":
        return "записать → " + target_text(item["target"])
    if item["fate"] == "merge":
        return "объединить с " + target_text(item["target"])
    if item["fate"] == "skip":
        return "не переносить"
    return "—"


def _answer_options(item: dict[str, Any]) -> str:
    if item["fate"] == "skip":
        return "принять (не переносить) · отклонить (перенести — предложение будет пересобрано)"
    if item["fate"] == "merge":
        return "принять · отклонить · переписать: «новая формулировка»"
    return "принять · отклонить · переписать: «новая формулировка» · объединить с записью: <id или карточка>"


def doubtful_items(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in evaluation["items"] if item["certainty"] == "doubtful"]


def render_section(evaluation: dict[str, Any]) -> list[str]:
    lines = ["## Учёт объектов разбора", ""]
    if not evaluation["present"]:
        return lines + [
            "Раздел `accounting` в предложении не заполнен: полнота переноса объектов разбора не проверена "
            "(предупреждение, применение не блокируется). Для полного учёта каждому объекту разбора дают одну судьбу — "
            "`record` / `merge` / `skip` (формат — `docs/SCRIPTS.md`).",
            "",
        ]
    summary = evaluation["summary"]
    digest = evaluation["decisions_sha256"]
    lines += [
        f"- Профиль разбора: `{summary['profile']}` — обязателен учёт объектов из " + ("01, 07, 08 (карта решений 09 профилем не строится)" if summary["profile"] == "light" else "01, 07, 08, 09"),
        f"- Объектов разбора: {summary['objects']}; пунктов учёта: {summary['items']}; не учтено: {len(summary['unaccounted'])}; двойной учёт: {len(summary['double'])}",
        f"- Записываем: {summary['record']}; объединяем: {summary['merge']}; не переносим: {summary['skip']}",
        f"- Уверенные: {summary['sure']}; сомнительные: {summary['doubtful']} (решено {summary['decided']}, ждут решения {len(summary['pending'])})",
        "- Решения по пунктам: " + (f"`{DECISIONS_FILE}`, хеш решений `{digest}`" if digest else "пока нет")
        + ". Новое решение меняет то, что будет записано: diff показывается заново, иначе apply откажет.",
        "",
        "### Уверенные — применяются общим подтверждением",
        "",
    ]
    sure = [item for item in evaluation["items"] if item["certainty"] == "sure"]
    for fate, title in FATES.items():
        group = [item for item in sure if item["fate"] == fate]
        if not group:
            continue
        lines += [f"**{title}** ({len(group)})", ""]
        for item in group:
            line = f"- `{item['ref']}` — {item['title']}: {item['label']}"
            if fate != "skip":
                line += f" → {target_text(item['target'])}"
            if item["reason"]:
                line += f" — почему: {item['reason']}"
            if item["decision"]:
                line += f" — решение пользователя: {decision_text(item['decision'])}"
            lines.append(line)
        lines.append("")
    if not sure:
        lines += ["Уверенных пунктов нет.", ""]

    doubtful = doubtful_items(evaluation)
    waiting = len(evaluation.get("pending") or [])
    lines += ["### Сомнительные — нужен ответ по каждому пункту" if waiting else "### Сомнительные — ответы получены", ""]
    if doubtful:
        lines += ["| № | Пункт | Объект | Предлагаем | Почему / вопрос | Решение пользователя |", "|---|---|---|---|---|---|"]
        for number, item in enumerate(doubtful, start=1):
            why = "; ".join(part for part in (item["reason"], item["question"]) if part)
            lines.append("| " + " | ".join(_cell(value) for value in (number, f"`{item['ref']}`", f"{item['title']}: {item['label']}", _proposed(item), why, decision_text(item["decision"]))) + " |")
        lines.append("")
    else:
        lines += ["Сомнительных пунктов нет.", ""]

    pending = [(number, item) for number, item in enumerate(doubtful, start=1) if item["decision"] is None]
    lines += ["### Вопросы пользователю — одним блоком", ""]
    if pending:
        lines += [
            "Задать одним сообщением, по каждому пункту — варианты ответа. Ответы записать "
            "`na.py knowledge decide --meeting … --item <пункт> --decision accept|reject|rewrite|merge [--text …] [--target …]`, "
            "затем показать diff заново. Без ответа пункт не применяется, а apply отказывает.",
            "",
        ]
        for number, item in pending:
            question = item["question"] or item["reason"] or "Подтвердите предложенное."
            lines.append(f"{number}. **`{item['ref']}`** — {item['label']}. Предлагаем: {_proposed(item)}. {question}")
            lines.append(f"   Ответ: {_answer_options(item)}")
        lines.append("")
    else:
        lines += ["Все сомнительные пункты решены." if doubtful else "Вопросов нет.", ""]

    if evaluation["untracked"]:
        lines += ["### Изменения предложения без пункта учёта", "", "Темы, факты и прочие сведения: у каждого изменения должно быть основание в разборе или в словах пользователя.", ""]
        lines += [f"- {entry}" for entry in evaluation["untracked"]]
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Команда knowledge decide
# ---------------------------------------------------------------------------


def _resolve_item(evaluation: dict[str, Any], raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    doubtful = doubtful_items(evaluation)
    if raw.isdigit():
        number = int(raw)
        if not 1 <= number <= len(doubtful):
            raise NaError(f"Сомнительного пункта №{number} нет: их {len(doubtful)}. Список — na.py knowledge decide --meeting … --list")
        return doubtful[number - 1]
    matches = [item for item in evaluation["items"] if item["ref"] == raw]
    if not matches:
        known = ", ".join(item["ref"] for item in doubtful) or "сомнительных нет"
        raise NaError(f"Пункт {raw!r} не найден в учёте предложения. Сомнительные пункты: {known}")
    if len(matches) > 1:
        raise NaError(f"Пункт {raw!r} учтён дважды — сначала исправьте предложение (одна судьба на объект)")
    return matches[0]


def _context(root: Path, meeting_id: str) -> tuple[Path, dict[str, Any], str, bool]:
    path = meeting_dir(root, meeting_id)
    proposal, proposal_sha = load_proposal(path)
    if "accounting" not in proposal:
        raise NaError("В предложении нет раздела accounting: решения по пунктам принимаются только по учтённым объектам. Дополните предложение учётом и покажите diff.")
    receipt = read_json_or_none(path / RECEIPT_FILE) or {}
    return path, proposal, proposal_sha, proposal_sha in receipt.get("applied_proposals", [])


def list_items(root: Path, meeting_id: str) -> dict[str, Any]:
    path, proposal, proposal_sha, applied = _context(root, meeting_id)
    evaluation = evaluate(root, path, proposal, proposal_sha)
    doubtful = doubtful_items(evaluation)
    return {
        "valid": not evaluation["errors"],
        "meeting_id": meeting_id,
        "applied": applied,
        "summary": evaluation["summary"],
        "doubtful": [
            {"number": number, "item": item["ref"], "object": f"{item['title']}: {item['label']}", "fate": item["fate"], "target": item["target"], "reason": item["reason"], "question": item["question"], "decision": item["decision"]}
            for number, item in enumerate(doubtful, start=1)
        ],
        "errors": evaluation["errors"],
    }


def record_decision(root: Path, meeting_id: str, item_raw: str, decision: str, text: str | None, target_raw: str | None) -> dict[str, Any]:
    path, proposal, proposal_sha, applied = _context(root, meeting_id)
    if applied:
        raise NaError("Предложение уже применено: решения по его пунктам базу не меняют. Изменение оформляется новым предложением.")
    if decision not in DECISIONS:
        raise NaError(f"--decision: допустимо {', '.join(DECISIONS)}")
    text = (text or "").strip() or None
    target_raw = (target_raw or "").strip() or None
    if decision == "rewrite" and not text:
        raise NaError("Для rewrite нужна новая формулировка: --text \"…\"")
    if decision == "merge" and not target_raw:
        raise NaError("Для merge нужна запись-цель: --target <id> | knowledge/….jsonl#<id> | knowledge/….md[#Раздел]")
    if decision in ("accept", "reject") and (text or target_raw):
        raise NaError(f"Для {decision} параметры --text и --target не используются")
    if decision == "rewrite" and target_raw:
        raise NaError("Для rewrite --target не используется: формулировка заменяется в цели пункта")
    if decision == "merge" and text:
        raise NaError("Для merge --text не используется")

    effective, _ = load_decisions(path, proposal_sha)
    current = evaluate(root, path, proposal, proposal_sha, decisions_override=effective)
    structural = current["structural_errors"] + plan_changes(root, proposal)["errors"]
    if structural:
        raise NaError(
            f"Сначала исправьте предложение ({len(structural)} ошибок предусловий, первая: {structural[0]}). "
            "Ответы по пунктам привязаны к версии предложения: после исправления они бы потерялись."
        )
    item = _resolve_item(current, item_raw)
    if item["fate"] in FATES:
        problem = _decision_problem(item, {"decision": decision, "text": text, "target": target_raw})
        if problem:
            raise NaError(f"Решение по пункту {item['ref']} не записано: {problem}")
    target: dict[str, str] | None = None
    if decision == "merge":
        target = _resolve_decision_target(target_raw, item["target"] or {})
    recorded_at = now_iso()
    line = {
        "decision_id": "kd-" + sha256_text("\x1f".join([meeting_id, proposal_sha, item["ref"], decision, text or "", canonical_json(target), recorded_at]))[:20],
        "meeting_id": meeting_id,
        "proposal_sha256": proposal_sha,
        "item": item["ref"],
        "decision": decision,
        "text": text,
        "target": target,
        "source": "user_confirmation",
        "recorded_at": recorded_at,
    }
    tentative = dict(effective)
    tentative[item["ref"]] = line
    check = evaluate(root, path, proposal, proposal_sha, decisions_override=tentative)
    problems = check["item_errors"].get(item["ref"]) or []
    if problems:
        raise NaError(f"Решение по пункту {item['ref']} не записано: " + "; ".join(problems))
    append_jsonl(path / DECISIONS_FILE, [line])
    note = "Решения изменились: покажите diff заново (na.py knowledge diff --meeting …); apply без нового diff будет отклонён."
    if item["ref"] in check["rebuild"]:
        note = "Пользователь просит перенести объект, который предложение не переносило: пересоберите предложение с записью этого объекта и задайте вопросы заново. До этого apply заблокирован."
    return {
        "valid": True,
        "meeting_id": meeting_id,
        "decision": line,
        "pending": check["pending"],
        "rebuild_required": check["rebuild"],
        "summary": check["summary"],
        "diff_required": True,
        "note": note,
    }
