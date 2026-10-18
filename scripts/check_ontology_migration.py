#!/usr/bin/env python3
"""Тест отсутствия потерь при миграции онтологий в core.ontology.yaml.

Что проверяет:
  1. Каждый идентификатор (сущность, поле, enum-значение, предикат связи,
     ключ раздела, текстовое правило) из двух исходных ontology.yaml покрыт
     записью в docs/ontology_migration.json.
  2. Для каждой записи цель существует: идентификатор (или его новое имя)
     встречается в core/ontology/core.ontology.yaml, core/ontology/profile.schema.json,
     profiles/**/*.yaml или core/contracts/*.json — либо потеря задокументирована
     (target=dropped/replaced с reason).
  3. Снимок идентификаторов в docs/ontology_migration.json совпадает с
     фактическим содержимым исходников (если исходники доступны).
  4. (опционально, при наличии PyYAML) профили profiles/base и
     profiles/examples валидны по profile.schema.json (минимальный валидатор
     подмножества JSON Schema draft 2020-12).

Зависимости: только стандартная библиотека. PyYAML — опциональная
зависимость только для шага 4; без него шаг пропускается с предупреждением.

Исходные онтологии — закрытые референсы и в репозиторий не входят. Пути к
ним передаются аргументами; без них проверка идёт по снимку идентификаторов,
сохранённому в docs/ontology_migration.json.

Использование:
  python3 scripts/check_ontology_migration.py
  python3 scripts/check_ontology_migration.py --cfo-source PATH --executive-source PATH
  python3 scripts/check_ontology_migration.py --cfo-source PATH --executive-source PATH --update-snapshot
  python3 scripts/check_ontology_migration.py --forbidden-pattern 'regex'   # доп. проверка приватности

Код выхода: 0 — потерь нет и все проверки пройдены; 1 — есть потери или ошибки.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_ONTOLOGY = ROOT / "core" / "ontology" / "core.ontology.yaml"
PROFILE_SCHEMA = ROOT / "core" / "ontology" / "profile.schema.json"
CONTRACTS_DIR = ROOT / "core" / "contracts"
PROFILES_DIR = ROOT / "profiles"
MIGRATION_JSON = ROOT / "docs" / "ontology_migration.json"

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
KEY_RE = re.compile(r"^(\s*)(-\s+)?(" + IDENT + r")\s*:(?:\s+(.*))?$")
LIST_RE = re.compile(r"^(\s*)-\s+(.*)$")
ENUM_RE = re.compile(r"enum\(([^)]*)\)")
BRACKET_RE = re.compile(r"^\[(.*)\]$")
BARE_RE = re.compile(r"^" + IDENT + r"$")
REL_RE = re.compile(r"^(" + IDENT + r")\s+(" + IDENT + r")\s+(\S+)")
BLOCK_SCALARS = {">-", "|", ">", "|-", "|+", ">+"}
VALID_TARGETS = {
    "core", "contracts", "role_profile", "organization_profile", "policy",
    "example_profile", "renamed", "replaced", "dropped",
}


# ----------------------------------------------------------------------
# 1. Сбор идентификаторов из YAML-подобного файла (без PyYAML)
# ----------------------------------------------------------------------
def strip_comment(line: str) -> str:
    out = []
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            out.append(ch)
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def harvest(path: Path, source: str) -> list[dict]:
    """Возвращает список {source, path, kind, ident} для файла онтологии."""
    items: list[dict] = []
    stack: list[tuple[int, str]] = []
    block_skip_indent: int | None = None
    list_counter: dict[str, int] = {}

    def add(node_path: str, kind: str, ident: str) -> None:
        items.append({"source": source, "path": node_path, "kind": kind, "ident": ident})

    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = strip_comment(raw.rstrip("\n"))
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            if block_skip_indent is not None:
                if indent > block_skip_indent:
                    continue
                block_skip_indent = None
            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = ".".join(n for _, n in stack)
            m = KEY_RE.match(line)
            if m:
                dash, key, value = m.group(2), m.group(3), m.group(4)
                node_path = f"{parent}.{key}" if parent else key
                add(node_path, "key", key)
                eff_indent = indent + (len(dash) if dash else 0)
                stack.append((eff_indent if dash else indent, key))
                if value is None:
                    continue
                value = value.strip()
                if value in BLOCK_SCALARS:
                    block_skip_indent = indent
                    stack.pop()
                    continue
                stack.pop()
                em = ENUM_RE.search(value)
                if em:
                    for v in em.group(1).split("|"):
                        v = v.strip()
                        if BARE_RE.match(v):
                            add(f"{node_path}.{v}", "enum_value", v)
                    continue
                bm = BRACKET_RE.match(value)
                if bm:
                    for v in bm.group(1).split(","):
                        v = v.strip().strip('"').strip("'")
                        if BARE_RE.match(v):
                            add(f"{node_path}.{v}", "list_value", v)
                continue
            lm = LIST_RE.match(line)
            if lm:
                item = lm.group(2).strip()
                if parent == "relations":
                    rm = REL_RE.match(item)
                    if rm:
                        add(f"relations.{rm.group(2)}", "relation", rm.group(2))
                    continue
                if BARE_RE.match(item):
                    add(f"{parent}.{item}", "list_value", item)
                    continue
                n = list_counter.get(parent, 0) + 1
                list_counter[parent] = n
                add(f"{parent}[{n}]", "text", item[:80])
    return items


# ----------------------------------------------------------------------
# 2. Целевые тексты
# ----------------------------------------------------------------------
def strip_yaml_comments(text: str) -> str:
    return "\n".join(strip_comment(l) for l in text.splitlines())


def load_targets() -> dict[str, str]:
    targets: dict[str, str] = {}
    targets["core"] = strip_yaml_comments(CORE_ONTOLOGY.read_text(encoding="utf-8"))
    targets["schema"] = PROFILE_SCHEMA.read_text(encoding="utf-8")
    contracts = []
    for p in sorted(CONTRACTS_DIR.glob("*.json")):
        contracts.append(p.read_text(encoding="utf-8"))
    targets["contracts"] = "\n".join(contracts)
    base, examples = [], []
    for p in sorted(PROFILES_DIR.rglob("*.yaml")):
        txt = strip_yaml_comments(p.read_text(encoding="utf-8"))
        (examples if "examples" in p.parts else base).append(txt)
    targets["profiles_base"] = "\n".join(base)
    targets["profiles_examples"] = "\n".join(examples)
    targets["profiles"] = targets["profiles_base"] + "\n" + targets["profiles_examples"]
    return targets


def word_in(ident: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(ident) + r"(?![A-Za-z0-9_])", text) is not None


TARGET_TEXT_KEYS = {
    "core": ["core"],
    "contracts": ["contracts"],
    "role_profile": ["schema", "profiles"],
    "organization_profile": ["schema", "profiles"],
    "policy": ["schema", "profiles"],
    "example_profile": ["profiles_examples"],
    "renamed": ["core", "schema", "profiles", "contracts"],
    "replaced": ["core", "schema", "profiles", "contracts"],
}


# ----------------------------------------------------------------------
# 3. Сопоставление путей с записями миграционной таблицы
# ----------------------------------------------------------------------
def pattern_to_regex(pattern: str) -> re.Pattern:
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("[*]", i):
            out.append(r"\[\d+\]")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(r".+")
            i += 2
            continue
        if ch == "*":
            out.append(r"[^.\[\]]+")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def specificity(pattern: str) -> int:
    return len(re.sub(r"\*|\[\*\]", "", pattern))


class Entry:
    def __init__(self, raw: dict, index: int):
        self.raw = raw
        self.index = index
        self.source = raw.get("source", "both")
        patterns = raw["match"]
        self.patterns = [patterns] if isinstance(patterns, str) else list(patterns)
        self.regexes = [(pattern_to_regex(p), specificity(p)) for p in self.patterns]
        self.target = raw["target"]
        self.hits = 0

    def match(self, source: str, path: str) -> int | None:
        if self.source not in ("both", source):
            return None
        best = None
        for rx, spec in self.regexes:
            if rx.match(path):
                best = spec if best is None else max(best, spec)
        return best


def find_entry(entries: list[Entry], source: str, path: str) -> Entry | None:
    best: tuple[int, int, Entry] | None = None
    for e in entries:
        spec = e.match(source, path)
        if spec is None:
            continue
        key = (spec, -e.index)
        if best is None or key > (best[0], best[1]):
            best = (spec, -e.index, e)
    return best[2] if best else None


# ----------------------------------------------------------------------
# 4. Минимальный валидатор JSON Schema (подмножество draft 2020-12)
# ----------------------------------------------------------------------
class SchemaValidator:
    def __init__(self, schema: dict):
        self.schema = schema

    def resolve(self, ref: str) -> dict:
        assert ref.startswith("#/"), ref
        node = self.schema
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def validate(self, instance, schema: dict, path: str, errors: list[str]) -> None:
        if "$ref" in schema:
            self.validate(instance, self.resolve(schema["$ref"]), path, errors)
            return
        if "anyOf" in schema:
            ok = False
            for sub in schema["anyOf"]:
                sub_err: list[str] = []
                self.validate(instance, sub, path, sub_err)
                if not sub_err:
                    ok = True
                    break
            if not ok:
                errors.append(f"{path}: не соответствует ни одному варианту anyOf")
            return
        if "oneOf" in schema:
            matches = 0
            for sub in schema["oneOf"]:
                sub_err = []
                self.validate(instance, sub, path, sub_err)
                if not sub_err:
                    matches += 1
            if matches != 1:
                errors.append(f"{path}: oneOf дал {matches} совпадений")
            return
        if "const" in schema and instance != schema["const"]:
            errors.append(f"{path}: ожидалось const {schema['const']!r}, получено {instance!r}")
            return
        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{path}: значение {instance!r} не входит в enum {schema['enum']}")
            return
        if "type" in schema and not self._type_ok(instance, schema["type"]):
            errors.append(f"{path}: ожидался тип {schema['type']}, получено {type(instance).__name__}")
            return
        if isinstance(instance, dict):
            props = schema.get("properties", {})
            for req in schema.get("required", []):
                if req not in instance:
                    errors.append(f"{path}: отсутствует обязательное поле {req!r}")
            for key, val in instance.items():
                if key in props:
                    self.validate(val, props[key], f"{path}.{key}", errors)
                elif schema.get("additionalProperties", True) is False:
                    errors.append(f"{path}: недопустимое поле {key!r}")
        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                errors.append(f"{path}: минимум {schema['minItems']} элементов")
            if "items" in schema:
                for i, val in enumerate(instance):
                    self.validate(val, schema["items"], f"{path}[{i}]", errors)
        if isinstance(instance, str) and "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: строка короче {schema['minLength']}")
        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(f"{path}: значение меньше {schema['minimum']}")

    @staticmethod
    def _type_ok(instance, typ) -> bool:
        types = typ if isinstance(typ, list) else [typ]
        for t in types:
            if t == "null" and instance is None:
                return True
            if t == "boolean" and isinstance(instance, bool):
                return True
            if t == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
                return True
            if t == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
                return True
            if t == "string" and isinstance(instance, str):
                return True
            if t == "object" and isinstance(instance, dict):
                return True
            if t == "array" and isinstance(instance, list):
                return True
        return False


def validate_profiles(report: list[str]) -> bool:
    try:
        import yaml  # type: ignore
    except ImportError:
        report.append("ПРЕДУПРЕЖДЕНИЕ: PyYAML не установлен — валидация профилей по profile.schema.json пропущена.")
        return True
    schema = json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8"))
    validator = SchemaValidator(schema)
    kinds = {"role.yaml": "role_profile", "organization.yaml": "organization_profile", "policy.yaml": "user_policy"}
    ok = True
    for p in sorted(PROFILES_DIR.rglob("*.yaml")):
        if p.name not in kinds:
            continue
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        errors: list[str] = []
        validator.validate(data, {"$ref": f"#/$defs/{kinds[p.name]}"}, p.relative_to(ROOT).as_posix(), errors)
        expected_synthetic = "examples" in p.parts
        if data.get("meta", {}).get("synthetic") is not expected_synthetic:
            errors.append(f"{p.relative_to(ROOT).as_posix()}: meta.synthetic должно быть {expected_synthetic}")
        if errors:
            ok = False
            report.append(f"ОШИБКА ПРОФИЛЯ {p.relative_to(ROOT).as_posix()}:")
            report.extend("  - " + e for e in errors)
        else:
            report.append(f"профиль валиден: {p.relative_to(ROOT).as_posix()}")
    return ok


# ----------------------------------------------------------------------
# 5. Основная проверка
# ----------------------------------------------------------------------
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cfo-source", help="путь к CFO-онтологии v0.4 (закрытый референс)")
    ap.add_argument("--executive-source", help="путь к онтологии владельца/CEO v1.1.0 (закрытый референс)")
    ap.add_argument("--update-snapshot", action="store_true", help="обновить снимок идентификаторов в docs/ontology_migration.json")
    ap.add_argument("--forbidden-pattern", help="regex; совпадение в core/, profiles/ или docs/ONTOLOGY_MIGRATION.md считается ошибкой приватности")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    report: list[str] = []
    failures: list[str] = []

    migration = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
    entries = [Entry(e, i) for i, e in enumerate(migration["entries"])]
    for e in entries:
        if e.target not in VALID_TARGETS:
            failures.append(f"запись #{e.index}: неизвестный target {e.target!r}")
        if e.target in ("dropped", "replaced") and not e.raw.get("reason"):
            failures.append(f"запись #{e.index} ({e.patterns}): target={e.target} требует reason")
        if e.target == "renamed" and not e.raw.get("renamed_to"):
            failures.append(f"запись #{e.index} ({e.patterns}): target=renamed требует renamed_to")
        if e.target == "replaced" and not e.raw.get("replaced_by"):
            failures.append(f"запись #{e.index} ({e.patterns}): target=replaced требует replaced_by")

    # --- идентификаторы исходников: из файлов или из снимка
    snapshot = migration.get("source_identifiers", [])
    harvested: list[dict] = []
    sources_available = bool(args.cfo_source and args.executive_source)
    if sources_available:
        cfo = Path(args.cfo_source)
        exe = Path(args.executive_source)
        if not cfo.exists() or not exe.exists():
            failures.append("указанные исходные онтологии не найдены")
        else:
            harvested = harvest(cfo, "cfo") + harvest(exe, "executive")
            report.append(f"собрано идентификаторов из исходников: cfo={sum(1 for h in harvested if h['source']=='cfo')}, executive={sum(1 for h in harvested if h['source']=='executive')}")
            if args.update_snapshot:
                migration["source_identifiers"] = harvested
                MIGRATION_JSON.write_text(json.dumps(migration, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                report.append("снимок идентификаторов обновлён в docs/ontology_migration.json")
                snapshot = harvested
            else:
                key = lambda h: (h["source"], h["path"], h["kind"], h["ident"])  # noqa: E731
                hs, ss = {key(h) for h in harvested}, {key(h) for h in snapshot}
                if hs != ss:
                    for k in sorted(hs - ss):
                        failures.append(f"снимок устарел: в исходнике есть, в снимке нет: {k}")
                    for k in sorted(ss - hs):
                        failures.append(f"снимок устарел: в снимке есть, в исходнике нет: {k}")
                else:
                    report.append("снимок идентификаторов совпадает с исходниками")
    else:
        report.append("исходные онтологии не переданы — проверка по снимку из docs/ontology_migration.json")
    identifiers = snapshot
    if not identifiers:
        failures.append("снимок идентификаторов пуст; запустите с --cfo-source/--executive-source --update-snapshot")

    targets = load_targets()

    uncovered: list[str] = []
    not_found: list[str] = []
    documented_drops: dict[str, list[str]] = {}
    for item in identifiers:
        entry = find_entry(entries, item["source"], item["path"])
        if entry is None:
            uncovered.append(f"{item['source']}:{item['path']} ({item['kind']})")
            continue
        entry.hits += 1
        ident = item["ident"]
        kind = item["kind"]
        tgt = entry.target
        if tgt in ("dropped", "replaced"):
            label = f"{tgt}: {entry.raw.get('reason')}"
            documented_drops.setdefault(label, []).append(f"{item['source']}:{item['path']}")
            if tgt == "replaced":
                rb = entry.raw["replaced_by"]
                if not any(word_in(rb, targets[k]) for k in TARGET_TEXT_KEYS["replaced"]):
                    not_found.append(f"{item['source']}:{item['path']} -> replaced_by {rb!r} не найден в целевых файлах")
            continue
        if kind == "text" or entry.raw.get("verify") is False:
            continue
        probe = entry.raw.get("renamed_to", ident) if tgt == "renamed" else ident
        if isinstance(probe, dict):
            probe = probe.get(ident, ident)
        if not any(word_in(probe, targets[k]) for k in TARGET_TEXT_KEYS[tgt]):
            not_found.append(f"{item['source']}:{item['path']} -> {probe!r} не найден в {TARGET_TEXT_KEYS[tgt]} (target={tgt})")

    stale = [f"#{e.index} {e.patterns} (target={e.target})" for e in entries if e.hits == 0]

    if uncovered:
        failures.append(f"не покрыто миграционной таблицей: {len(uncovered)} идентификаторов")
        failures.extend("  - " + u for u in uncovered)
    if not_found:
        failures.append(f"не найдено в целевых файлах: {len(not_found)} идентификаторов")
        failures.extend("  - " + n for n in not_found)

    # --- валидация профилей
    if not validate_profiles(report):
        failures.append("профили не прошли валидацию по profile.schema.json")

    # --- проверка приватности (опционально)
    if args.forbidden_pattern:
        rx = re.compile(args.forbidden_pattern, re.IGNORECASE)
        scan = [CORE_ONTOLOGY, PROFILE_SCHEMA, ROOT / "docs" / "ONTOLOGY_MIGRATION.md", MIGRATION_JSON]
        scan += sorted(PROFILES_DIR.rglob("*.yaml"))
        for p in scan:
            if p.exists() and rx.search(p.read_text(encoding="utf-8")):
                failures.append(f"проверка приватности: запрещённый шаблон найден в {p.relative_to(ROOT).as_posix()}")

    # --- вывод
    print("== check_ontology_migration ==")
    for line in report:
        print(line)
    print(f"идентификаторов проверено: {len(identifiers)}; записей таблицы: {len(entries)}; "
          f"задокументированных отбрасываний/замен: {sum(len(v) for v in documented_drops.values())}")
    if args.verbose or documented_drops:
        print("-- задокументированные отбрасывания и замены (не считаются потерей):")
        for label, paths in sorted(documented_drops.items()):
            print(f"  [{len(paths)}] {label}")
            if args.verbose:
                for pth in paths:
                    print("      " + pth)
    if stale:
        print(f"-- предупреждение: записи таблицы без совпадений ({len(stale)}):")
        for s in stale:
            print("  " + s)
    if failures:
        print("-- ОШИБКИ:")
        for f in failures:
            print("  " + f)
        print("РЕЗУЛЬТАТ: ПОТЕРИ ИЛИ ОШИБКИ ОБНАРУЖЕНЫ")
        return 1
    print("РЕЗУЛЬТАТ: потерь нет")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
