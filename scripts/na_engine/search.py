"""Локальный полнотекстовый индекс (SQLite FTS5, при его отсутствии — резервный LIKE-поиск)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .common import NaError, meeting_dirs, now_iso, read_json_or_none, read_jsonl, read_text, relative, write_json
from .registries import organization_dirs


DATABASE_RELATIVE = "system/search.sqlite"
MANIFEST_RELATIVE = "system/search_index_manifest.json"
TRANSCRIPT_RE = re.compile(r"^\s*(?:\[(?P<timestamp>[^\]]+)\]\s*)?(?P<speaker>[^:\[\]]{1,60}?):\s+(?P<text>.*)$")
COLUMNS = ("doc_id", "kind", "meeting_id", "timestamp", "person_id", "topic_id", "title", "text", "source_path", "source_ref")


def doc_id(*parts: str | None) -> str:
    raw = "\x1f".join(part or "" for part in parts).encode("utf-8")
    return "doc-" + hashlib.sha256(raw).hexdigest()[:20]


def document(kind: str, meeting_id: str | None, timestamp: str | None, person_id: str | None, topic_id: str | None, title: str, text: str, source_path: str, source_ref: str) -> tuple[str, ...]:
    return (doc_id(kind, meeting_id, source_path, source_ref, text), kind, meeting_id or "", timestamp or "", person_id or "", topic_id or "", title, text, source_path, source_ref)


def meeting_documents(root: Path, meeting_path: Path) -> list[tuple[str, ...]]:
    meeting_id = meeting_path.name
    analysis = meeting_path / "analysis"
    extraction = read_json_or_none(analysis / "01_extraction.json") or {}
    names = {item.get("full_name"): item.get("person_id") for item in extraction.get("participants", []) if isinstance(item, dict)}
    docs: list[tuple[str, ...]] = []
    transcript_path = meeting_path / "transcript.txt"
    for line_number, line in enumerate(read_text(transcript_path).splitlines(), 1):
        match = TRANSCRIPT_RE.match(line)
        if not match:
            if line.strip():
                docs.append(document("transcript_quote", meeting_id, None, None, None, "transcript", line.strip(), relative(transcript_path, root), f"line:{line_number}"))
            continue
        speaker = match.group("speaker").strip()
        text = match.group("text").strip()
        if text:
            docs.append(document("transcript_quote", meeting_id, match.group("timestamp"), names.get(speaker), None, speaker, text, relative(transcript_path, root), f"line:{line_number}"))

    structured_specs = [
        ("01_extraction.json", "key_facts", "key_fact", "fact", "quote", "timestamp", None, None),
        ("01_extraction.json", "topics", "topic", "title", "source_quote", "source_timestamp", None, "id"),
        ("04_arguments.json", "arguments", "argument", "claim", None, None, "speaker", "topic"),
        ("07_commitments.json", "commitments", "commitment", "what", "source_quote", "source_timestamp", "who", "linked_topic"),
        ("07_commitments.json", "open_questions", "open_question", "question", "quote", "timestamp", None, "topic"),
        ("08_risks.json", "risks", "risk", "description", None, None, None, "linked_topic"),
        ("09_decision_authority.json", "decision_areas", "decision", "decision", None, None, "proposal_owner", "topic"),
    ]
    for filename, list_key, kind, title_key, quote_key, timestamp_key, person_key, topic_key in structured_specs:
        path = analysis / filename
        data = read_json_or_none(path)
        if not isinstance(data, dict):
            continue
        for index, entry in enumerate(data.get(list_key, [])):
            if not isinstance(entry, dict):
                continue
            title = str(entry.get(title_key) or kind)
            pieces = [title]
            if quote_key and entry.get(quote_key):
                pieces.append(str(entry[quote_key]))
            if kind == "argument":
                pieces.extend(str(entry.get(key) or "") for key in ("evidence", "warrant", "counter_argument"))
                pieces.extend(str(item.get("quote") or "") for item in entry.get("source_quotes", []))
            elif kind == "risk":
                pieces.append(str((entry.get("mitigation") or {}).get("planned_actions") or ""))
                pieces.extend(str(item.get("quote") or "") for item in entry.get("evidence", []))
            elif kind == "decision":
                pieces.append(str(entry.get("current_state") or ""))
                pieces.extend(str(option) for option in entry.get("options", []))
                pieces.extend(str(item.get("quote") or "") for item in entry.get("evidence", []))
            docs.append(document(kind, meeting_id, str(entry.get(timestamp_key) or "") if timestamp_key else None, str(entry.get(person_key) or "") if person_key else None, str(entry.get(topic_key) or "") if topic_key else None, title, "\n".join(piece for piece in pieces if piece), relative(path, root), f"$.{list_key}[{index}]"))

    tactics_path = analysis / "02_tactics.json"
    tactics = read_json_or_none(tactics_path) or {}
    for family in ("cialdini", "voss"):
        for index, entry in enumerate(tactics.get(family, [])):
            docs.append(document("tactic", meeting_id, entry.get("source_timestamp"), entry.get("used_by"), None, f"{family}: {entry.get('tactic_type')}", "\n".join(str(value) for value in (entry.get("source_quote"), entry.get("explanation"), entry.get("counter_move")) if value), relative(tactics_path, root), f"$.{family}[{index}]"))
    manip_path = analysis / "03_manipulations.json"
    manipulations = read_json_or_none(manip_path) or {}
    for index, entry in enumerate(manipulations.get("manipulations", [])):
        docs.append(document("manipulation", meeting_id, entry.get("source_timestamp"), entry.get("used_by"), None, str(entry.get("type") or "manipulation"), "\n".join(str(value) for value in (entry.get("source_quote"), entry.get("how_recognized")) if value), relative(manip_path, root), f"$.manipulations[{index}]"))
    return docs


def knowledge_documents(root: Path) -> list[tuple[str, ...]]:
    docs: list[tuple[str, ...]] = []
    specs = [(root / "knowledge/people", "person_profile", "person"), (root / "knowledge/topics", "topic_profile", "topic"), (root / "knowledge/tactics_observed", "tactic_profile", "tactic")]
    specs += [(path, "organization_profile", "organization") for path in organization_dirs(root)]
    for directory, kind, prefix in specs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            text = read_text(path)
            heading = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), path.stem)
            person_id = path.stem.split("--", 1)[0] if kind in {"person_profile", "tactic_profile"} else None
            topic_id = path.stem if kind == "topic_profile" else None
            docs.append(document(kind, None, None, person_id, topic_id, f"{prefix}: {heading}", text, relative(path, root), "$"))
    jsonl_specs = [
        ("knowledge/decisions/register.jsonl", "executive_decision"),
        ("knowledge/assignments/register.jsonl", "assignment"),
        ("knowledge/commitments/open.jsonl", "open_commitment"),
        ("knowledge/commitments/closed.jsonl", "closed_commitment"),
        ("knowledge/risks/active.jsonl", "active_risk"),
        ("knowledge/risks/closed.jsonl", "closed_risk"),
    ]
    for relative_path, kind in jsonl_specs:
        for line_number, record in enumerate(read_jsonl(root / relative_path), start=1):
            title = str(record.get("statement") or record.get("title") or record.get("what") or record.get("description") or record.get("id") or kind)
            person_id = str(record.get("person_id") or record.get("assignee") or record.get("owner") or record.get("decision_owner") or record.get("who") or "")
            docs.append(document(kind, str(record.get("meeting_id") or "") or None, None, person_id or None, str(record.get("topic_id") or record.get("linked_topic") or "") or None, title, json.dumps(record, ensure_ascii=False, sort_keys=True), relative_path, f"line:{line_number}"))
    return docs


def fts_available(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("CREATE VIRTUAL TABLE temp.__fts_probe USING fts5(x)")
        connection.execute("DROP TABLE temp.__fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def build_index(root: Path) -> dict[str, Any]:
    docs: list[tuple[str, ...]] = []
    for meeting_path in meeting_dirs(root):
        docs.extend(meeting_documents(root, meeting_path))
    docs.extend(knowledge_documents(root))
    database = root / DATABASE_RELATIVE
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()
    connection = sqlite3.connect(database)
    try:
        engine = "fts5" if fts_available(connection) else "like"
        if engine == "fts5":
            connection.execute("CREATE VIRTUAL TABLE search_index USING fts5(doc_id UNINDEXED, kind, meeting_id UNINDEXED, timestamp UNINDEXED, person_id UNINDEXED, topic_id UNINDEXED, title, text, source_path UNINDEXED, source_ref UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
        else:
            connection.execute("CREATE TABLE search_index(doc_id TEXT, kind TEXT, meeting_id TEXT, timestamp TEXT, person_id TEXT, topic_id TEXT, title TEXT, text TEXT, source_path TEXT, source_ref TEXT)")
        connection.execute("CREATE TABLE search_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(f"INSERT INTO search_index({','.join(COLUMNS)}) VALUES({','.join('?' for _ in COLUMNS)})", docs)
        generated_at = now_iso()
        connection.executemany("INSERT INTO search_meta(key,value) VALUES(?,?)", [("index_version", "1.1.0"), ("engine", engine), ("generated_at", generated_at), ("documents", str(len(docs)))])
        connection.commit()
    finally:
        connection.close()
    counts = Counter(item[1] for item in docs)
    manifest = {"search_index_version": "1.1.0", "engine": engine, "generated_at": generated_at, "database": DATABASE_RELATIVE, "documents": len(docs), "by_kind": dict(sorted(counts.items()))}
    write_json(root / MANIFEST_RELATIVE, manifest)
    return {"valid": True, **manifest}


def fts_query(raw: str, match_any: bool) -> tuple[str, list[str]]:
    tokens = re.findall(r"[\w]+", raw, flags=re.UNICODE)
    if not tokens:
        raise NaError("Запрос не содержит поисковых слов")
    operator = " OR " if match_any else " AND "
    return operator.join('"' + token.replace('"', '""') + '"' for token in tokens), tokens


def search(root: Path, query_text: str, kind: str | None = None, meeting_id: str | None = None, person_id: str | None = None, topic_id: str | None = None, limit: int = 20, match_any: bool = False) -> dict[str, Any]:
    database = root / DATABASE_RELATIVE
    rebuilt = False
    if not database.is_file():
        build_index(root)
        rebuilt = True
    query, tokens = fts_query(query_text, match_any)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        engine = connection.execute("SELECT value FROM search_meta WHERE key='engine'").fetchone()
        engine = engine[0] if engine else "fts5"
        filters: list[str] = []
        params: list[object] = []
        for column, value in (("kind", kind), ("meeting_id", meeting_id), ("person_id", person_id), ("topic_id", topic_id)):
            if value:
                filters.append(f"{column} = ?")
                params.append(value)
        limit = max(1, min(limit, 200))
        if engine == "fts5":
            where = " AND ".join(["search_index MATCH ?", *filters])
            sql = f"SELECT doc_id, kind, meeting_id, timestamp, person_id, topic_id, title, snippet(search_index, 7, '⟦', '⟧', ' … ', 24) AS snippet, source_path, source_ref, bm25(search_index) AS rank FROM search_index WHERE {where} ORDER BY rank LIMIT ?"
            rows = connection.execute(sql, [query, *params, limit]).fetchall()
        else:
            joiner = " OR " if match_any else " AND "
            like = joiner.join("(lower(title) LIKE ? OR lower(text) LIKE ?)" for _ in tokens)
            like_params: list[object] = []
            for token in tokens:
                like_params += [f"%{token.lower()}%", f"%{token.lower()}%"]
            where = " AND ".join([f"({like})", *filters])
            sql = f"SELECT doc_id, kind, meeting_id, timestamp, person_id, topic_id, title, substr(text, 1, 160) AS snippet, source_path, source_ref, 0 AS rank FROM search_index WHERE {where} LIMIT ?"
            rows = connection.execute(sql, [*like_params, *params, limit]).fetchall()
        results = [dict(row) for row in rows]
    finally:
        connection.close()
    return {"valid": True, "query": query_text, "fts_query": query, "engine": engine, "index_rebuilt": rebuilt, "count": len(results), "results": results}


def format_results(payload: dict[str, Any]) -> list[str]:
    lines = [f"Найдено: {payload['count']} (движок: {payload['engine']})"]
    for index, item in enumerate(payload["results"], 1):
        location = item["meeting_id"] or "knowledge"
        timestamp = f" · {item['timestamp']}" if item["timestamp"] else ""
        lines.append(f"{index}. [{item['kind']}] {item['title']} · {location}{timestamp}")
        lines.append(f"   {str(item['snippet']).replace(chr(10), ' ')}")
        lines.append(f"   {item['source_path']} {item['source_ref']}")
    return lines
