"""Детерминированная сборка report_auto.md с блоком «Один экран» и проверка его полноты."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    ANALYSIS_FILES,
    NaError,
    gate_state,
    meeting_dir,
    meeting_meta,
    read_json,
    read_json_or_none,
    write_json,
    write_text,
)
from .registries import meeting_commitments, meeting_risks


REQUIRED_HEADINGS = [
    "## Один экран",
    "## Результат",
    "## Участники",
    "## Ключевые факты",
    "## Обязательства: актуальное состояние",
    "## Наблюдённые тактики",
    "## Манипуляции",
    "## Карта решений и полномочий",
    "## Карта аргументов",
    "## Динамика участия",
    "## Эмоциональная динамика по тексту",
    "## Эффективность встречи",
    "## Риски: актуальное состояние",
    "## Протокол следующей встречи",
    "## Контроль качества",
]

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
EFFECTIVENESS_LABELS = {
    "effective": "результативная",
    "partial_effectiveness": "частично результативная",
    "low_effectiveness": "низкая результативность",
}
DIMENSION_LABELS = {
    "agenda_coverage": "Охват повестки",
    "decision_closure": "Закрытие решений",
    "actionability": "Исполнимость договорённостей",
    "role_clarity": "Ясность ролей",
    "evidence_quality": "Качество обоснований",
    "participation_balance": "Баланс участия",
}
OUTCOME_LABELS = {
    "agreed": "договорённость достигнута",
    "partial": "частичная договорённость",
    "deferred": "решение отложено",
    "failed": "договорённость не достигнута",
    "no_decision": "решение не принималось",
}
SOURCE_LABELS = {"user_confirmed": "подтверждено пользователем", "user_confirmation": "подтверждено пользователем"}


def load_analysis(meeting_path: Path) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = {}
    for name in ANALYSIS_FILES:
        path = meeting_path / "analysis" / name
        if not path.is_file():
            raise NaError(f"Нет аналитического файла {name}; отчёт собирается только из полного набора 11 JSON")
        data[name[:2]] = read_json(path)
    return data


def name_map(extraction: dict[str, Any]) -> dict[str, str]:
    return {p["person_id"]: p["full_name"] for p in extraction.get("participants", []) if isinstance(p, dict)}


def person(value: str | None, names: dict[str, str]) -> str:
    if value is None:
        return "не определён"
    return names.get(value, value)


def cell(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def table_row(values: list[Any]) -> str:
    return "| " + " | ".join(cell(v) for v in values) + " |"


def evidence_text(items: list[dict[str, Any]]) -> str:
    return "; ".join(f"«{e.get('quote')}» (`{e.get('timestamp')}`)" for e in items if isinstance(e, dict))


def one_screen(data: dict[str, dict[str, Any]], names: dict[str, str], commitments: list[dict[str, Any]], risks: list[dict[str, Any]]) -> list[str]:
    """Первый экран: главный вывод, требуемое решение, ключевые факты, риски, варианты, следующий шаг."""
    extraction, decisions, effectiveness = data["01"], data["09"], data["11"]
    lines = ["## Один экран", ""]

    outcome = OUTCOME_LABELS.get(extraction.get("outcome"), extraction.get("outcome") or "не определён")
    classification = EFFECTIVENESS_LABELS.get(effectiveness.get("classification"), effectiveness.get("classification"))
    lines += [
        "### Главный вывод",
        "",
        f"{extraction.get('outcome_summary', 'Результат не сформирован.')} "
        f"Итог: **{outcome}**; рабочая оценка встречи **{effectiveness.get('overall_score_0_5')} из 5** ({classification}).",
        "",
    ]
    if effectiveness.get("confidence") in {"possible", "speculative"}:
        lines += [f"_Уверенность оценки низкая (`{effectiveness['confidence']}`): выводы требуют проверки._", ""]

    lines += ["### Требуемое решение", ""]
    pending = [
        d for d in decisions.get("decision_areas", [])
        if isinstance(d, dict) and not any(word in (d.get("current_state") or "").lower() for word in ("принято", "утвержд", "закрыт"))
    ]
    for d in pending or decisions.get("decision_areas", []):
        maker = person(d.get("final_decision_maker"), names)
        deadline = d.get("deadline") or "срок не определён"
        lines.append(f"- **{d.get('decision')}** — состояние: {d.get('current_state')}; ЛПР: {maker}; срок: {deadline}; confidence: `{d.get('confidence')}`.")
    for item in decisions.get("unresolved", []):
        lines.append(f"- Не закрыто: {item}")
    if not decisions.get("decision_areas") and not decisions.get("unresolved"):
        lines.append("- Решений, требующих вмешательства, по транскрипту не выделено.")

    lines += ["", "### Ключевые факты", ""]
    facts = sorted(
        [f for f in extraction.get("key_facts", []) if isinstance(f, dict)],
        key=lambda f: {"certain": 0, "probable": 1, "possible": 2, "speculative": 3}.get(f.get("confidence"), 9),
    )[:5]
    for fact in facts:
        lines.append(f"- {fact.get('fact')} (`{fact.get('timestamp')}`, confidence: `{fact.get('confidence')}`).")
    open_commitments = [c for c in commitments if c.get("status") not in {"done", "cancelled"}]
    if open_commitments:
        nearest = sorted(open_commitments, key=lambda c: c.get("deadline") or "9999-12-31")[0]
        lines.append(
            f"- Обязательств по встрече: {len(commitments)}, открытых: {len(open_commitments)}; ближайший срок — "
            f"{nearest.get('deadline') or 'не установлен'}: {person(nearest.get('who'), names)} — {nearest.get('what')}."
        )
    if not facts and not open_commitments:
        lines.append("- Ключевые факты не выделены.")

    lines += ["", "### Риски", ""]
    active_risks = sorted(
        [r for r in risks if r.get("status") not in {"closed"}],
        key=lambda r: (SEVERITY_ORDER.get(r.get("severity"), 9), r.get("id") or ""),
    )[:5]
    for r in active_risks:
        mitigation = r.get("mitigation") or {}
        lines.append(
            f"- **{r.get('severity')}** — {r.get('description')} Владелец: {person(mitigation.get('owner'), names)}; "
            f"срок: {mitigation.get('deadline') or 'не установлен'}; статус: `{r.get('status')}`."
        )
    if not active_risks:
        lines.append("- Активных рисков по встрече не выделено.")

    lines += ["", "### Варианты", ""]
    options_found = False
    for d in decisions.get("decision_areas", []):
        for option in d.get("options", []) or []:
            options_found = True
            lines.append(f"- {d.get('decision')}: {option}")
    if not options_found:
        lines.append("- Альтернативные варианты в транскрипте не зафиксированы.")

    lines += ["", "### Следующий шаг", ""]
    protocol = effectiveness.get("next_meeting_protocol", [])[:3]
    for index, item in enumerate(protocol, 1):
        lines.append(f"{index}. {item}")
    if open_commitments:
        lines.append(f"{len(protocol) + 1}. Проконтролировать ближайшее обязательство: {person(nearest.get('who'), names)} — {nearest.get('what')} (срок {nearest.get('deadline') or 'не установлен'}).")
    if not protocol and not open_commitments:
        lines.append("1. Следующий шаг не определён; требуется решение пользователя.")
    lines += ["", "_Детали ниже раскрывают каждый пункт первого экрана; критические факты и риски высокой важности показаны здесь и не скрыты._", ""]
    return lines


def build_report(root: Path, meeting_path: Path) -> str:
    data = load_analysis(meeting_path)
    extraction = data["01"]
    names = name_map(extraction)
    meta = meeting_meta(meeting_path)
    gate = gate_state(meeting_path)
    excluded = extraction.get("excluded_source_range") or {}
    commitments = meeting_commitments(root, meeting_path)
    risks = meeting_risks(root, meeting_path)
    sensitivity = gate["sensitivity"] if gate["sensitivity"] != "not_set" else (meta.get("sensitivity") or "не указана")

    lines: list[str] = [
        f"# Автоматический отчёт: {meta['title']}",
        "",
        f"**Meeting ID:** `{meeting_path.name}`  ",
        f"**Дата:** {meta.get('date') or 'не указана'}  ",
        f"**Чувствительность:** `{sensitivity}`  ",
        f"**Шлюз согласия:** `{gate['consent']}`; маскирование: `{gate['masking_mode']}`; маршрут: `{gate['allowed_routes']}`  ",
        f"**Исключено с:** `{excluded.get('from', '—')}` — {excluded.get('reason', '—')}",
        "",
    ]
    lines += one_screen(data, names, commitments, risks)

    lines += ["## Результат", "", extraction.get("outcome_summary", "Результат не сформирован."), "", "## Участники", "", "| Участник | Роль | Сторона | Источник идентификации | Confidence |", "|---|---|---|---|---|"]
    for p in extraction.get("participants", []):
        source = p.get("identity_source")
        lines.append(table_row([p.get("full_name"), p.get("role_freeform") or p.get("role"), "наша" if p.get("is_our_side") else "контрагент", SOURCE_LABELS.get(source, source), p.get("confidence")]))

    lines += ["", "## Ключевые факты", ""]
    for fact in extraction.get("key_facts", []):
        lines.append(f"- {fact['fact']} — «{fact['quote']}» (`{fact['timestamp']}`, confidence: `{fact['confidence']}`).")
    if not extraction.get("key_facts"):
        lines.append("- Ключевые факты не выделены.")

    lines += ["", "## Обязательства: актуальное состояние", "", "| Кто | Действие | Исходный срок | Контрольная дата | Статус | Источник записи | Доказательство |", "|---|---|---|---|---|---|---|"]
    for c in commitments:
        follow = c.get("follow_up") or {}
        origin = "база знаний" if c.get("record_origin") == "knowledge" else "анализ (в базу не внесено)"
        lines.append(table_row([
            person(c.get("who"), names), c.get("what"), c.get("deadline"), follow.get("reminder_date"),
            c.get("status"), origin, f"«{c.get('source_quote')}» (`{c.get('source_timestamp')}`)",
        ]))
    if not commitments:
        lines.append(table_row(["—", "Обязательства не найдены", "—", "—", "—", "—", "—"]))

    open_questions = data["07"].get("open_questions", [])
    if open_questions:
        lines += ["", "Открытые вопросы из транскрипта:", ""]
        for q in open_questions:
            lines.append(f"- {q['question']} — «{q['quote']}» (`{q['timestamp']}`).")

    tactics = data["02"]
    lines += ["", "## Наблюдённые тактики", ""]
    found = False
    for family in ("cialdini", "voss"):
        for t in tactics.get(family, []):
            found = True
            lines.append(f"- `{family}:{t['tactic_type']}` — {person(t.get('used_by'), names)}: «{t['source_quote']}» (`{t['source_timestamp']}`); effect: `{t['effect']}`, confidence: `{t['confidence']}`. {t.get('explanation', '')}")
    if not found:
        lines.append("- Тактики влияния по транскрипту не выделены.")

    manip = data["03"]
    lines += ["", "## Манипуляции", ""]
    for m in manip.get("manipulations", []):
        lines.append(f"- `{m['type']}` (severity: `{m['severity']}`) — {person(m.get('used_by'), names)}: «{m['source_quote']}» (`{m['source_timestamp']}`); распознано: {m['how_recognized']}; effect: `{m['effect']}`, confidence: `{m['confidence']}`.")
    lines += ["", manip.get("conclusion", "Вывод отсутствует."), ""]

    decision = data["09"]
    lines += ["## Карта решений и полномочий", "", f"Статус карты: `{decision.get('status')}`", "", "| Решение | Состояние | Финальный ЛПР | Владелец | Срок | Доказательство |", "|---|---|---|---|---|---|"]
    for d in decision.get("decision_areas", []):
        owner = person(d.get("proposal_owner"), names)
        if d.get("proposal_owner_source") == "user_confirmation":
            owner += " (подтверждено пользователем)"
        lines.append(table_row([d["decision"], d["current_state"], person(d.get("final_decision_maker"), names), owner, d.get("deadline"), evidence_text(d.get("evidence", []))]))
    if decision.get("authority_observations"):
        lines += ["", "Наблюдения о полномочиях:", ""]
        for obs in decision["authority_observations"]:
            lines.append(f"- {person(obs.get('person_id'), names)}: {obs.get('observed_role')}; формальные полномочия: {obs.get('formal_authority_in_decision') or 'не установлены'}; confidence: `{obs.get('confidence')}`.")
    lines += ["", "Незакрытые вопросы:", ""]
    lines.extend(f"- {item}" for item in decision.get("unresolved", []))
    if not decision.get("unresolved"):
        lines.append("- Нет.")

    amap = data["10"]
    lines += ["", "## Карта аргументов", ""]
    for cluster in amap.get("argument_clusters", []):
        lines += [f"### {cluster['question']}", ""]
        for p in cluster.get("positions", []):
            lines.append(f"- **{person(p['speaker'], names)}:** {p['position']} Сила: {p['strength']}/5. Не хватает: {p['missing_evidence']}. {evidence_text(p.get('evidence', []))}")
        lines += ["", f"Статус: `{cluster['resolution']}` — {cluster['resolution_note']}", ""]
    if not amap.get("argument_clusters"):
        lines += ["Карта аргументов пуста.", ""]

    power = data["05"]
    shares = ", ".join(f"{person(x['person_id'], names)} — {x['share_pct']}%" for x in power.get("talk_time_share", []))
    lines += ["## Динамика участия", "", f"Доли речи: {shares or 'не рассчитаны'}.", "", power.get("evidence_note", ""), ""]
    if power.get("framing_moments"):
        lines += ["Контроль рамки обсуждения:", ""]
        for moment in power["framing_moments"]:
            lines.append(f"- {person(moment['speaker'], names)}: {moment['description']} — «{moment['quote']}» (`{moment['timestamp']}`).")
    if power.get("concessions"):
        lines += ["", "Уступки:", ""]
        for item in power["concessions"]:
            lines.append(f"- {person(item['speaker'], names)}: «{item['quote']}» (`{item['timestamp']}`) после позиции «{item['prior_position_quote']}» (`{item['prior_position_timestamp']}`).")

    emotions = data["06"]
    summary = emotions.get("overall_summary") or {}
    lines += [
        "",
        "## Эмоциональная динамика по тексту",
        "",
        f"Источник оценки: `{emotions.get('source')}`. Общий фон: `{summary.get('overall')}`.",
        "",
        summary.get("general_trend", "Обобщение отсутствует."),
        "",
        f"Наиболее напряжённый момент по текстовой оценке: `{summary.get('most_tense_timestamp', '—')}`. {emotions.get('limitations', '')}",
        "",
    ]

    effectiveness = data["11"]
    lines += [
        "## Эффективность встречи",
        "",
        f"**{effectiveness['overall_score_0_5']} из 5 — {EFFECTIVENESS_LABELS.get(effectiveness['classification'], effectiveness['classification'])}.**",
        "",
        effectiveness["method"],
        "",
        "| Измерение | Оценка | Основание |",
        "|---|---:|---|",
    ]
    for d in effectiveness.get("dimensions", []):
        lines.append(table_row([DIMENSION_LABELS.get(d["dimension"], d["dimension"]), d["score_0_5"], d["basis"]]))
    if effectiveness.get("strengths"):
        lines += ["", "Сильные стороны:", ""] + [f"- {item}" for item in effectiveness["strengths"]]
    if effectiveness.get("losses"):
        lines += ["", "Потери:", ""] + [f"- {item}" for item in effectiveness["losses"]]

    lines += ["", "## Риски: актуальное состояние", "", "| Важность | Статус | Риск | Владелец | Срок | Действие | Источник записи |", "|---|---|---|---|---|---|---|"]
    for r in risks:
        mitigation = r.get("mitigation") or {}
        origin = "база знаний" if r.get("record_origin") == "knowledge" else "анализ (в базу не внесено)"
        lines.append(table_row([r.get("severity"), r.get("status"), r.get("description"), person(mitigation.get("owner"), names), mitigation.get("deadline"), mitigation.get("planned_actions"), origin]))
    if not risks:
        lines.append(table_row(["—", "—", "Риски не выделены", "—", "—", "—", "—"]))

    lines += ["", "## Протокол следующей встречи", ""]
    lines.extend(f"{i}. {item}" for i, item in enumerate(effectiveness.get("next_meeting_protocol", []), 1))

    audio_path = meeting_path / "analysis" / "audio_acoustic_metrics.json"
    if audio_path.is_file():
        lines += ["", "## Аудиослой", "", "Инструментальные акустические метрики рассчитаны отдельно (analysis/audio_acoustic_metrics.json). Они не содержат смысловых или клинических выводов и не изменяют текстовый анализ сами по себе."]

    validation = read_json_or_none(meeting_path / "analysis" / "validation_report.json")
    lines += ["", "## Контроль качества", ""]
    if validation:
        lines.append(
            f"Валидатор: `valid={str(validation.get('valid')).lower()}`; схема: `{validation.get('schema_version')}`; "
            f"JSON: {validation.get('parsed_analysis_files')}/{validation.get('required_analysis_files')}; "
            f"проверено цитат: {validation.get('checked_quotes')}; ошибок: {len(validation.get('errors', []))}; "
            f"предупреждений: {len(validation.get('warnings', []))}."
        )
    else:
        lines.append("Валидация не выполнялась: запустите `na.py validate --meeting …`.")

    lines += ["", "---", "", "_Отчёт собран детерминированно из JSON-артефактов встречи и актуальных реестров knowledge/. Интерпретации остаются предметом человеческой проверки._", ""]
    return "\n".join(lines)


def validate_report(root: Path, meeting_path: Path, report_name: str = "report_auto.md") -> dict[str, Any]:
    report_path = meeting_path / report_name
    report = report_path.read_text(encoding="utf-8-sig") if report_path.is_file() else ""
    errors: list[str] = []
    if not report:
        errors.append(f"missing or empty report: {report_name}")
    errors.extend(f"missing heading: {h}" for h in REQUIRED_HEADINGS if h not in report)
    extraction = read_json_or_none(meeting_path / "analysis" / "01_extraction.json") or {}
    errors.extend(
        f"participant absent from report: {p['full_name']}"
        for p in extraction.get("participants", [])
        if isinstance(p, dict) and p.get("full_name") and p["full_name"] not in report
    )
    commitments = meeting_commitments(root, meeting_path)
    errors.extend(f"commitment absent from report: {c.get('id')}" for c in commitments if (c.get("what") or "") not in report)
    risks = meeting_risks(root, meeting_path)
    errors.extend(f"risk absent from report: {r.get('id')}" for r in risks if (r.get("description") or "") not in report)
    result = {
        "meeting_id": meeting_path.name,
        "report": report_name,
        "valid": not errors,
        "required_headings": len(REQUIRED_HEADINGS),
        "participants_checked": len(extraction.get("participants", [])),
        "commitments_checked": len(commitments),
        "risks_checked": len(risks),
        "errors": errors,
    }
    write_json(meeting_path / "analysis" / "report_auto_validation.json", result)
    return result


def run_report(root: Path, meeting_id: str) -> dict[str, Any]:
    path = meeting_dir(root, meeting_id)
    text = build_report(root, path)
    write_text(path / "report_auto.md", text)
    validation = validate_report(root, path)
    return {
        "valid": validation["valid"],
        "meeting_id": meeting_id,
        "report": "report_auto.md",
        "report_validation": validation,
    }
