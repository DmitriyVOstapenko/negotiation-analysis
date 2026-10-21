"""Детерминированная сборка report_auto.md с блоком «Один экран» и проверка его полноты."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .analysis_profile import ARTIFACT_LABELS, PROFILE_LABELS, profile_summary
from .common import (
    NaError,
    gate_state,
    meeting_dir,
    meeting_meta,
    read_json,
    read_json_or_none,
    read_text,
    today_iso,
    write_json,
    write_text,
)
from .quote_check import LEVEL_LABELS, plural_ru, transcript_quality
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
CONFIDENCE_ORDER = {"certain": 0, "probable": 1, "possible": 2, "speculative": 3}
# ниже probable: на первый экран не выносится (кроме рисков high — с пометкой), в таблицах — с пометкой
LOW_CONFIDENCE = {"possible", "speculative"}
OUTSIDER_MARK = "не участник встречи"
# id человека в артефактах: латиница в нижнем регистре, цифры, дефисы («p-nordtech-ceo»); свободный текст
# («не установлен: сторона «Береста Ритейл» (…)») — не id: печатается как есть, без пометки
_PERSON_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
# статус решения — только явный префикс current_state: «Принято: …», «Решено. …», «Согласовано — …»,
# «Утверждено: …» (без учёта регистра); всё остальное — открытое, без эвристик по тексту
_TAKEN_STATUS = re.compile(r"(принято|решено|согласовано|утверждено)\s*[:.—]\s*", re.IGNORECASE)
TAKEN_MARK = "записано как принятое"
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


LIGHT_HEADINGS = [
    "## Один экран",
    "## Результат",
    "## Участники",
    "## Ключевые факты",
    "## Обязательства: актуальное состояние",
    "## Риски: актуальное состояние",
    "## Не выполнялось по профилю",
    "## Контроль качества",
]
MAP_STATUS_LABELS = {"confirmed": "подтверждена пользователем", "proposed": "предложена, не подтверждена"}


def speaker_map_label(review: dict[str, Any], transcript_labels: list[str] | None = None) -> str:
    """Статус карты спикеров для «Данные:» и «Качества источника».

    Статусы движка — pending | proposed | confirmed (review.yaml). `speakers set` с частью меток
    ставит confirmed на всю карту, а остальные метки остаются с person_name: null, — такая карта
    подтверждена частично: перечисляются метки без сопоставления.
    """
    mapping = review.get("speaker_mapping") if isinstance(review.get("speaker_mapping"), dict) else {}
    status = mapping.get("status")
    if status != "confirmed":
        return MAP_STATUS_LABELS.get(status, "не подтверждена")
    entries = [item for item in mapping.get("speakers") or [] if isinstance(item, dict)]
    mapped = {str(item.get("label")) for item in entries if item.get("label") and (item.get("person_name") or item.get("person_id"))}
    unmapped = [str(item.get("label") or "без метки") for item in entries if not (item.get("person_name") or item.get("person_id"))]
    unmapped += [label for label in transcript_labels or [] if label not in mapped and label not in unmapped]
    if not mapped and entries:
        return "не подтверждена"
    if unmapped:
        return f"подтверждена частично (без сопоставления: {', '.join(unmapped)})"
    return MAP_STATUS_LABELS["confirmed"]


def load_analysis(meeting_path: Path) -> dict[str, dict[str, Any]]:
    """Артефакты профиля разбора: full — 11 JSON, light — 01, 07, 08 (остальные не читаются)."""
    data: dict[str, dict[str, Any]] = {}
    required = profile_summary(meeting_path)["required"]
    for name in required:
        path = meeting_path / "analysis" / name
        if not path.is_file():
            raise NaError(f"Нет аналитического файла {name}; отчёт собирается только из полного набора артефактов профиля ({len(required)} JSON)")
        data[name[:2]] = read_json(path)
    return data


def data_line(meeting_path: Path, meta: dict[str, Any], gate: dict[str, Any], validation: dict[str, Any] | None, profile: dict[str, Any]) -> str:
    """Строка «Данные:»: источник и его разметка, карта спикеров, проверка цитат, профиль, чего нет."""
    quality = transcript_quality(read_text(meeting_path / "transcript.txt"))
    provider = (meta.get("source") or {}).get("provider") or "провайдер не указан"
    labels = quality["speaker_labels"]
    source = (
        f"транскрипт ({provider}, реплик {quality['segments']}; таймкоды {'есть' if quality['has_timestamps'] else 'нет'}; "
        f"разметка говорящих {'есть — ' + plural_ru(len(labels), 'метка', 'метки', 'меток') if labels else 'нет'})"
    )
    speaker_map = f"карта спикеров {speaker_map_label(gate['review'], labels)}"
    stats = (validation or {}).get("quote_stats")
    if stats:
        quotes = (
            f"цитаты: {stats['checked']} проверено — буквально {stats['exact']}, после нормализации {stats['normalized']}, "
            f"с пропуском «…» {stats['ellipsis']}, предупреждений проверки {len(validation.get('warnings', []))}"
        )
    else:
        quotes = "строгая проверка цитат не выполнялась (na.py validate)"
    scope = "разбор полный (11 артефактов)" if profile["profile"] == "full" else f"{PROFILE_LABELS['light']}: {', '.join(name[:2] for name in profile['required'])}"
    missing = []
    if not quality["has_timestamps"]:
        missing.append("таймкодов")
    if not labels:
        missing.append("разметки говорящих")
    if not (meeting_path / "analysis" / "audio_acoustic_metrics.json").is_file():
        missing.append("аудиослоя")
    if profile["skipped_by_profile"]:
        missing.append(f"артефактов {', '.join(name[:2] for name in profile['skipped_by_profile'])} (не выполнялись по профилю)")
    tail = f"; нет: {', '.join(missing)}" if missing else ""
    return f"**Данные:** {source}, {speaker_map}; {quotes}; {scope}; реестры базы на {today_iso()}{tail}."


def source_quality_lines(validation: dict[str, Any] | None, review: dict[str, Any] | None = None) -> list[str]:
    """Раздел «Качество источника»: разметка, уровни совпадения цитат, атрибуция, таймкоды."""
    lines = ["", "### Качество источника", ""]
    stats = (validation or {}).get("quote_stats")
    quality = (validation or {}).get("source_quality")
    if not stats or not quality:
        return lines + ["Строгая проверка цитат не выполнялась: запустите `na.py validate --meeting …`."]
    labels = ", ".join(quality.get("speaker_labels") or []) or "нет"
    unmapped = quality.get("unmapped_labels") or []
    # статус — по текущему review.yaml (частичная карта видна по меткам без имени); без него — по отчёту проверки
    map_status = (
        speaker_map_label(review, quality.get("speaker_labels") or []) if review is not None
        else MAP_STATUS_LABELS.get(quality.get("speaker_map_status"), "не подтверждена")
    )
    lines += [
        f"- Запись: реплик {quality['segments']}; с таймкодом {quality['timestamped_segments']}; разметка говорящих: {labels}; "
        f"карта спикеров: {map_status}"
        + (f"; не сопоставлены с участниками при проверке цитат: {', '.join(unmapped)}" if unmapped else "") + ".",
        f"- Цитаты: проверено {stats['checked']} — буквально {stats['exact']}, после нормализации {stats['normalized']}, "
        f"с пропуском «…» {stats['ellipsis']}; не найдено {stats['not_found']}; склеек реплик разных говорящих {stats['spliced']}.",
        f"- Атрибуция говорящих: проверено {stats['attribution_checked']} — совпало {stats['attribution_matched']}, "
        f"расхождений {stats['attribution_mismatched']}, не проверено {stats['attribution_unverified']}.",
        f"- Таймкоды: допуск ±{validation.get('timestamp_tolerance_sec')} с; расхождений выше допуска {stats['timestamp_drift']}; "
        f"указывают на другую реплику {stats['timestamp_other_reply']}.",
    ]
    loose = [item for item in validation.get("quote_checks", []) if item.get("level") in ("normalized", "ellipsis")]
    if loose:
        lines.append("- Цитаты не дословно — сверить с записью:")
        for item in loose:
            labels_text = ", ".join(item.get("speaker_labels") or []) or "говорящий не размечен"
            lines.append(f"  - `{item['file']} {item['path']}` — {LEVEL_LABELS[item['level']]} (`{item.get('timestamp') or '—'}`, {labels_text}).")
    warnings = validation.get("warnings", [])
    if warnings:
        lines.append(f"- Предупреждения проверки ({len(warnings)}):")
        lines.extend(f"  - {item}" for item in warnings[:20])
        if len(warnings) > 20:
            lines.append(f"  - … ещё {len(warnings) - 20} в analysis/validation_report.json")
    return lines


# конец предложения: не меньше трёх знаков слова перед точкой (инициалы «Г. П.» не режутся), дальше — заглавная
_SENTENCE_BREAK = re.compile(r"(?<=[^\s.]{3}[.!?])\s+(?=[«\"(A-ZА-ЯЁ])|;\s*")


def short_role(text: Any) -> str:
    """Первое предложение наблюдаемой роли — подпись к человеку вне встречи; целиком роль — в карте полномочий."""
    value = clause(text)
    return clause(_SENTENCE_BREAK.split(value, maxsplit=1)[0]) or value


def name_map(extraction: dict[str, Any], decisions: dict[str, Any] | None = None) -> dict[str, str]:
    """person_id → подпись: участники — по имени из 01; люди вне встречи из 09.authority_observations — по наблюдаемой роли."""
    names = {p["person_id"]: p["full_name"] for p in extraction.get("participants", []) if isinstance(p, dict) and p.get("person_id")}
    for obs in (decisions or {}).get("authority_observations", []) or []:
        pid = obs.get("person_id") if isinstance(obs, dict) else None
        if is_person_id(pid) and pid not in names and short_role(obs.get("observed_role")):
            names[pid] = f"{short_role(obs.get('observed_role'))} ({OUTSIDER_MARK})"
    return names


def is_person_id(value: Any) -> bool:
    """Строка вида id человека («p-nordtech-ceo»), а не свободный текст."""
    return isinstance(value, str) and bool(_PERSON_ID.fullmatch(value))


def outsider(value: Any) -> str:
    """Человек не из участников без подписи: id — в обратных кавычках с пометкой «(не участник встречи)»;
    свободный текст («не установлен: сторона …») — как есть: это не id и не обязательно человек вне встречи."""
    return f"`{value}` ({OUTSIDER_MARK})" if is_person_id(value) else str(value)


def person(value: str | None, names: dict[str, str]) -> str:
    """Подпись человека: имя участника или роль из 09; id вне списка участников — в обратных кавычках
    с пометкой, а не сырым; свободный текст — как есть."""
    if value is None:
        return "не определён"
    if value in names:
        return names[value]
    return outsider(value)


# сокращения, точку после которых не снимаем: «2026 г.», «180 тыс. руб.», «и т. д.»
_ABBREVIATIONS = {"г", "гг", "руб", "коп", "тыс", "млн", "млрд", "шт", "мес", "мин", "сек", "ч", "т", "д", "п", "е", "др", "пр", "см", "стр", "кв", "ул"}
_LAST_WORD = re.compile(r"([A-Za-zА-Яа-яЁё]+)\.$")


def clause(value: Any) -> str:
    """Текст поля для вставки внутрь фразы: без завершающих «.», «;», «,», «:» (иначе на стыке — «.;» и «..»)."""
    text = str(value if value is not None else "").strip()
    while text and text[-1] in ".;,:" and not text.endswith("..."):
        word = _LAST_WORD.search(text)
        if text[-1] == "." and word and word.group(1).lower() in _ABBREVIATIONS:
            break
        text = text[:-1].rstrip()
    return text


def sentence(value: Any) -> str:
    """Текст поля как законченное предложение: ровно один знак конца."""
    text = clause(value)
    if not text:
        return ""
    return text if text[-1] in ".!?…" else text + "."


def deadline_text(item: dict[str, Any]) -> str | None:
    """Срок обязательства: дата; без даты — срок словами (deadline_verbatim) с пометкой «даты нет»."""
    if item.get("deadline"):
        return str(item["deadline"])
    verbatim = clause(item.get("deadline_verbatim"))
    return f"{verbatim} (даты нет)" if verbatim else None


def low_confidence(item: dict[str, Any]) -> bool:
    return item.get("confidence") in LOW_CONFIDENCE


def decision_status(state: Any) -> tuple[str | None, str]:
    """(статус, остаток) по явному префиксу current_state: «Принято: аванс 20%» → ("принято", "аванс 20%").
    Без префикса — (None, состояние целиком): решение открытое. «Договорённости нет: решили вернуться…»,
    «Согласовано только с юристами…», «Решение принято, но требует утверждения СД», старые артефакты без
    префикса — открытые (безопасная сторона: открытое не выдаётся за принятое)."""
    text = str(state if state is not None else "").strip()
    match = _TAKEN_STATUS.match(text)
    if not match:
        return None, text
    return match.group(1).lower(), text[match.end():].strip()


def decision_taken(state: Any) -> bool:
    """Решение записано как принятое — только по статусному префиксу current_state (decision_status)."""
    return decision_status(state)[0] is not None


def risk_order(risk: dict[str, Any]) -> tuple[int, int, str]:
    """Сначала high, внутри важности — по уверенности (certain → speculative)."""
    return SEVERITY_ORDER.get(risk.get("severity"), 9), CONFIDENCE_ORDER.get(risk.get("confidence"), 2), risk.get("id") or ""


def commitment_label(item: dict[str, Any], names: dict[str, str]) -> str:
    """«Исполнитель — действие» для первого экрана; ниже probable — с пометкой «(требует подтверждения)»."""
    mark = " (требует подтверждения)" if low_confidence(item) else ""
    return f"{person(item.get('who'), names)} — {clause(item.get('what'))}{mark}"


def related_mentions_text(mentions: Any) -> str:
    """related_mentions обязательства одной строкой: «связанные реплики: 00:01:57 (предложение), 00:03:21»."""
    parts = []
    for mention in mentions if isinstance(mentions, list) else []:
        if isinstance(mention, dict) and mention.get("timestamp"):
            note = clause(mention.get("note"))
            parts.append(f"{mention['timestamp']} ({note})" if note else str(mention["timestamp"]))
    return f"связанные реплики: {', '.join(parts)}" if parts else ""


def confidence_cell(item: dict[str, Any], low_note: str) -> str | None:
    value = item.get("confidence")
    return f"{value} — {low_note}" if value in LOW_CONFIDENCE else value


def cell(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def table_row(values: list[Any]) -> str:
    return "| " + " | ".join(cell(v) for v in values) + " |"


def evidence_text(items: list[dict[str, Any]]) -> str:
    return "; ".join(f"«{e.get('quote')}» (`{e.get('timestamp')}`)" for e in items if isinstance(e, dict))


def decision_line(area: dict[str, Any], names: dict[str, str], mark: str = "") -> str:
    """Строка «Требуемого решения»: формулировка, состояние, ЛПР, срок, confidence."""
    return (
        f"- **{clause(area.get('decision'))}**{mark} — состояние: {clause(area.get('current_state')) or 'не указано'}; "
        f"ЛПР: {person(area.get('final_decision_maker'), names)}; срок: {area.get('deadline') or 'не определён'}; "
        f"confidence: `{area.get('confidence')}`."
    )


def excluded_line(excluded: dict[str, Any], review: dict[str, Any] | None) -> str | None:
    """Исключённый фрагмент записи: «с `from` до `to`»; `to: null` — «до конца записи». Конец — из
    01.excluded_source_range.to; в старых артефактах без поля — из review.yaml source_scope.excluded_until, если есть."""
    if not (excluded.get("from") or excluded.get("reason")):
        return None
    end = excluded.get("to")
    if end is None and "to" not in excluded:
        scope = (review or {}).get("source_scope")
        end = scope.get("excluded_until") if isinstance(scope, dict) else None
    until = f"до `{end}`" if end else "до конца записи"
    return f"**Исключено из анализа:** с `{excluded.get('from') or '—'}` {until} — {sentence(excluded.get('reason')) or 'причина не указана.'}"


def one_screen(data: dict[str, dict[str, Any]], names: dict[str, str], commitments: list[dict[str, Any]], risks: list[dict[str, Any]]) -> list[str]:
    """Первый экран: главный вывод, требуемое решение, ключевые факты, риски, варианты, следующий шаг."""
    # лёгкий разбор: 09 (решения) и 11 (эффективность) не строились — так и пишем, а не «не выделено»
    light = "09" not in data or "11" not in data
    extraction, decisions, effectiveness = data["01"], data.get("09") or {}, data.get("11") or {}
    lines = ["## Один экран", ""]

    outcome = OUTCOME_LABELS.get(extraction.get("outcome"), extraction.get("outcome") or "не определён")
    classification = EFFECTIVENESS_LABELS.get(effectiveness.get("classification"), effectiveness.get("classification"))
    score = (
        "оценка эффективности не выполнялась (лёгкий разбор)" if light
        else f"рабочая оценка встречи **{effectiveness.get('overall_score_0_5')} из 5** ({classification})"
    )
    lines += [
        "### Главный вывод",
        "",
        f"{extraction.get('outcome_summary', 'Результат не сформирован.')} "
        f"Итог: **{outcome}**; {score}.",
        "",
    ]
    if effectiveness.get("confidence") in {"possible", "speculative"}:
        lines += [f"_Уверенность оценки низкая (`{effectiveness['confidence']}`): выводы требуют проверки._", ""]

    lines += ["### Требуемое решение", ""]
    areas = [d for d in decisions.get("decision_areas", []) if isinstance(d, dict)]
    # показываются все области 09: сначала открытые, затем записанные как принятые — только по статусному
    # префиксу current_state («Принято: …», «Решено. …»); без префикса — открытое (старые артефакты тоже)
    open_areas = [d for d in areas if not decision_taken(d.get("current_state"))]
    taken_areas = [d for d in areas if decision_taken(d.get("current_state"))]
    unresolved = decisions.get("unresolved", []) or []
    for d in open_areas:
        lines.append(decision_line(d, names))
    for item in unresolved:
        lines.append(f"- Не закрыто: {sentence(item)}")
    if light:
        lines.append("- Карта решений и полномочий не строилась (лёгкий разбор).")
        for question in data["07"].get("open_questions", []):
            lines.append(f"- Открытый вопрос: {clause(question.get('question'))} (`{question.get('timestamp')}`).")
    elif areas and not open_areas and not unresolved:
        lines.append("- Открытых решений нет: все решения карты записаны как принятые.")
    elif not areas and not unresolved:
        lines.append("- Решений, требующих вмешательства, по транскрипту не выделено.")
    for d in taken_areas:
        lines.append(decision_line(d, names, f" ({TAKEN_MARK})"))

    lines += ["", "### Ключевые факты", ""]
    # факты ниже probable на первый экран не выносятся (report-format.md); они — в разделе «Ключевые факты» ниже
    facts = sorted(
        [f for f in extraction.get("key_facts", []) if isinstance(f, dict) and not low_confidence(f)],
        key=lambda f: CONFIDENCE_ORDER.get(f.get("confidence"), 9),
    )[:5]
    for fact in facts:
        lines.append(f"- {clause(fact.get('fact'))} (`{fact.get('timestamp')}`, confidence: `{fact.get('confidence')}`).")
    open_commitments = [c for c in commitments if c.get("status") not in {"done", "cancelled"}]
    unconfirmed = [c for c in open_commitments if low_confidence(c)]
    if open_commitments:
        nearest = sorted(open_commitments, key=lambda c: c.get("deadline") or "9999-12-31")[0]
        lines.append(
            f"- Обязательств по встрече: {len(commitments)}, открытых: {len(open_commitments)}"
            + (f", из них требуют подтверждения (confidence ниже probable): {len(unconfirmed)}" if unconfirmed else "")
            + f"; ближайший срок — {deadline_text(nearest) or 'не установлен'}: {commitment_label(nearest, names)}."
        )
    if not facts and not open_commitments:
        lines.append("- Ключевые факты не выделены.")

    lines += ["", "### Риски", ""]
    # на первом экране — certain/probable, сначала high; риск high с confidence ниже probable не скрывается,
    # но идёт с пометкой; остальные риски ниже probable — только в разделе «Риски: актуальное состояние»
    active = [r for r in risks if r.get("status") not in {"closed"}]
    eligible = [r for r in active if not low_confidence(r) or r.get("severity") == "high"]
    shown = sorted(eligible, key=risk_order)[:5]
    for r in shown:
        mitigation = r.get("mitigation") or {}
        mark = f" _(требует проверки: confidence `{r.get('confidence')}`)_" if low_confidence(r) else ""
        lines.append(
            f"- **{r.get('severity')}**{mark} — {sentence(r.get('description'))} Владелец: {person(mitigation.get('owner'), names)}; "
            f"срок: {mitigation.get('deadline') or 'не установлен'}; статус: `{r.get('status')}`."
        )
    if not shown:
        lines.append("- Активных рисков с уверенностью не ниже probable не выделено." if active else "- Активных рисков по встрече не выделено.")
    if len(eligible) > len(shown):
        lines.append(f"- Ещё {plural_ru(len(eligible) - len(shown), 'риск', 'риска', 'рисков')} — в разделе «Риски: актуальное состояние».")
    hidden_low = len(active) - len(eligible)
    if hidden_low:
        lines.append(
            f"- Ещё {plural_ru(hidden_low, 'риск', 'риска', 'рисков')} с уверенностью ниже probable — не на первом экране, "
            "требуют проверки: см. «Риски: актуальное состояние»."
        )

    lines += ["", "### Варианты", ""]
    options_found = False
    for d in areas:
        for option in d.get("options", []) or []:
            options_found = True
            lines.append(f"- {clause(d.get('decision'))}: {option}")
    if light:
        lines.append("- Варианты решений не выделялись (лёгкий разбор).")
    elif not options_found:
        lines.append("- Альтернативные варианты в транскрипте не зафиксированы.")

    lines += ["", "### Следующий шаг", ""]
    protocol = effectiveness.get("next_meeting_protocol", [])[:3]
    for index, item in enumerate(protocol, 1):
        lines.append(f"{index}. {item}")
    if open_commitments:
        lines.append(f"{len(protocol) + 1}. Проконтролировать ближайшее обязательство: {commitment_label(nearest, names)} (срок {deadline_text(nearest) or 'не установлен'}).")
    if not protocol and not open_commitments:
        lines.append("1. Следующий шаг не определён; требуется решение пользователя.")
    if light:
        number = len(protocol) + (1 if open_commitments else 0) + (0 if protocol or open_commitments else 1) + 1
        lines.append(f"{number}. Если нужен полный разбор (тактики, аргументация, карта решений, оценка встречи): `na.py profile set --meeting … --profile full` и недостающие артефакты.")
    lines += ["", "_Детали ниже раскрывают каждый пункт первого экрана; критические факты и риски высокой важности показаны здесь и не скрыты._", ""]
    return lines


def build_report(root: Path, meeting_path: Path) -> str:
    data = load_analysis(meeting_path)
    extraction = data["01"]
    names = name_map(extraction, data.get("09"))
    participant_ids = {p.get("person_id") for p in extraction.get("participants", []) if isinstance(p, dict)}
    meta = meeting_meta(meeting_path)
    gate = gate_state(meeting_path)
    excluded = extraction.get("excluded_source_range") or {}
    commitments = meeting_commitments(root, meeting_path)
    risks = meeting_risks(root, meeting_path)
    sensitivity = gate["sensitivity"] if gate["sensitivity"] != "not_set" else (meta.get("sensitivity") or "не указана")
    profile = profile_summary(meeting_path)
    validation = read_json_or_none(meeting_path / "analysis" / "validation_report.json")

    lines: list[str] = [
        f"# Автоматический отчёт: {meta['title']}",
        "",
        data_line(meeting_path, meta, gate, validation, profile),
        "",
        f"**Meeting ID:** `{meeting_path.name}`  ",
        f"**Дата:** {meta.get('date') or 'не указана'}  ",
        f"**Чувствительность:** `{sensitivity}`  ",
        f"**Шлюз согласия:** `{gate['consent']}`; маскирование: `{gate['masking_mode']}`; маршрут: `{gate['allowed_routes']}`  ",
    ]
    exclusion = excluded_line(excluded, gate.get("review"))
    if exclusion:
        lines.append(exclusion)
    lines.append("")
    lines += one_screen(data, names, commitments, risks)

    lines += ["## Результат", "", extraction.get("outcome_summary", "Результат не сформирован."), "", "## Участники", "", "| Участник | Роль | Сторона | Источник идентификации | Confidence |", "|---|---|---|---|---|"]
    for p in extraction.get("participants", []):
        source = p.get("identity_source")
        lines.append(table_row([p.get("full_name"), p.get("role_freeform") or p.get("role"), "наша" if p.get("is_our_side") else "контрагент", SOURCE_LABELS.get(source, source), p.get("confidence")]))

    lines += ["", "## Ключевые факты", ""]
    for fact in extraction.get("key_facts", []):
        lines.append(f"- {clause(fact['fact'])} — «{fact['quote']}» (`{fact['timestamp']}`, confidence: `{fact['confidence']}`).")
    if not extraction.get("key_facts"):
        lines.append("- Ключевые факты не выделены.")

    lines += ["", "## Обязательства: актуальное состояние", "", "| Кто → кому | Действие | Исходный срок | Контрольная дата | Статус | Источник записи | Доказательство |", "|---|---|---|---|---|---|---|"]
    for c in commitments:
        follow = c.get("follow_up") or {}
        origin = "база знаний" if c.get("record_origin") == "knowledge" else "анализ (в базу не внесено)"
        parties = person(c.get("who"), names) + (f" → {person(c.get('to_whom'), names)}" if c.get("to_whom") else "")
        status = f"{c.get('status')}; confidence `{c.get('confidence')}` — требует подтверждения" if low_confidence(c) else c.get("status")
        lines.append(table_row([
            parties, c.get("what"), deadline_text(c), follow.get("reminder_date"),
            status, origin, f"«{c.get('source_quote')}» (`{c.get('source_timestamp')}`)",
        ]))
        mentions = related_mentions_text(c.get("related_mentions"))
        if mentions:  # необязательное поле; без него строка записи — как раньше
            lines.append("| ↳ | " + cell(mentions) + " |  |  |  |  |  |")
    if not commitments:
        lines.append(table_row(["—", "Обязательства не найдены", "—", "—", "—", "—", "—"]))

    open_questions = data["07"].get("open_questions", [])
    if open_questions:
        lines += ["", "Открытые вопросы из транскрипта:", ""]
        for q in open_questions:
            lines.append(f"- {clause(q['question'])} — «{q['quote']}» (`{q['timestamp']}`).")

    if profile["profile"] == "light":
        return "\n".join(lines + closing_sections(meeting_path, names, risks, None, validation, profile))

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
        lines.append(f"- `{m['type']}` (severity: `{m['severity']}`) — {person(m.get('used_by'), names)}: «{m['source_quote']}» (`{m['source_timestamp']}`); распознано: {clause(m['how_recognized'])}; effect: `{m['effect']}`, confidence: `{m['confidence']}`.")
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
            pid = obs.get("person_id")
            # человек вне встречи: подпись по id (его роль — сразу за двоеточием, повторять её незачем)
            who = person(pid, names) if pid in participant_ids else outsider(pid)
            lines.append(
                f"- {who}: {clause(obs.get('observed_role'))}; формальные полномочия: "
                f"{clause(obs.get('formal_authority_in_decision')) or 'не установлены'}; confidence: `{obs.get('confidence')}`."
            )
    lines += ["", "Незакрытые вопросы:", ""]
    lines.extend(f"- {item}" for item in decision.get("unresolved", []))
    if not decision.get("unresolved"):
        lines.append("- Нет.")

    amap = data["10"]
    lines += ["", "## Карта аргументов", ""]
    for cluster in amap.get("argument_clusters", []):
        lines += [f"### {cluster['question']}", ""]
        for p in cluster.get("positions", []):
            lines.append(f"- **{person(p['speaker'], names)}:** {sentence(p['position'])} Сила: {p['strength']}/5. Не хватает: {sentence(p['missing_evidence'])} {evidence_text(p.get('evidence', []))}")
        lines += ["", f"Статус: `{cluster['resolution']}` — {cluster['resolution_note']}", ""]
    if not amap.get("argument_clusters"):
        lines += ["Карта аргументов пуста.", ""]

    power = data["05"]
    shares = ", ".join(f"{person(x['person_id'], names)} — {x['share_pct']}%" for x in power.get("talk_time_share", []))
    lines += ["## Динамика участия", "", f"Доли речи: {shares or 'не рассчитаны'}.", "", power.get("evidence_note", ""), ""]
    if power.get("framing_moments"):
        lines += ["Контроль рамки обсуждения:", ""]
        for moment in power["framing_moments"]:
            lines.append(f"- {person(moment['speaker'], names)}: {clause(moment['description'])} — «{moment['quote']}» (`{moment['timestamp']}`).")
    if power.get("concessions"):
        lines += ["", "Уступки:", ""]
        for item in power["concessions"]:
            # prior_position_speaker (необязательно): прежнюю позицию назвал другой человек — показать, кто
            prior_speaker = item.get("prior_position_speaker")
            prior_by = f"{person(prior_speaker, names)}: " if prior_speaker and prior_speaker != item["speaker"] else ""
            lines.append(f"- {person(item['speaker'], names)}: «{item['quote']}» (`{item['timestamp']}`) после позиции {prior_by}«{item['prior_position_quote']}» (`{item['prior_position_timestamp']}`).")

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

    return "\n".join(lines + closing_sections(meeting_path, names, risks, effectiveness, validation, profile))


def closing_sections(meeting_path: Path, names: dict[str, str], risks: list[dict[str, Any]], effectiveness: dict[str, Any] | None, validation: dict[str, Any] | None, profile: dict[str, Any]) -> list[str]:
    """Риски, протокол (full) или «не выполнялось по профилю» (light), аудио, контроль качества."""
    # все риски встречи (проверка полноты ищет каждый здесь); сначала high; ниже probable — с пометкой «требует проверки»
    lines = ["", "## Риски: актуальное состояние", "", "| Важность | Уверенность | Статус | Риск | Владелец | Срок | Действие | Источник записи |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(risks, key=risk_order):
        mitigation = r.get("mitigation") or {}
        origin = "база знаний" if r.get("record_origin") == "knowledge" else "анализ (в базу не внесено)"
        lines.append(table_row([
            r.get("severity"), confidence_cell(r, "требует проверки"), r.get("status"), r.get("description"),
            person(mitigation.get("owner"), names), mitigation.get("deadline"), mitigation.get("planned_actions"), origin,
        ]))
    if not risks:
        lines.append(table_row(["—", "—", "—", "Риски не выделены", "—", "—", "—", "—"]))

    if effectiveness is not None:
        lines += ["", "## Протокол следующей встречи", ""]
        lines.extend(f"{i}. {item}" for i, item in enumerate(effectiveness.get("next_meeting_protocol", []), 1))
    if profile["skipped_by_profile"]:
        lines += ["", "## Не выполнялось по профилю", "", f"Профиль разбора: **{PROFILE_LABELS[profile['profile']]}**. Не выполнялись — это не пробел в данных и не ошибка:", ""]
        for name in profile["skipped_by_profile"]:
            present = " — файл есть и проверен валидатором, но в отчёт этого профиля не входит" if name in profile["present_outside_profile"] else ""
            lines.append(f"- `{name}` — {ARTIFACT_LABELS.get(name, name)}{present}.")
        lines += ["", "Перейти к полному разбору: `na.py profile set --meeting … --profile full`, подготовить недостающие артефакты и повторить `na.py refresh --meeting …`."]

    audio_path = meeting_path / "analysis" / "audio_acoustic_metrics.json"
    if audio_path.is_file():
        lines += ["", "## Аудиослой", "", "Инструментальные акустические метрики рассчитаны отдельно (analysis/audio_acoustic_metrics.json). Они не содержат смысловых или клинических выводов и не изменяют текстовый анализ сами по себе."]

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
    lines += source_quality_lines(validation, gate_state(meeting_path)["review"])

    lines += ["", "---", "", "_Отчёт собран детерминированно из JSON-артефактов встречи и актуальных реестров knowledge/. Интерпретации остаются предметом человеческой проверки._", ""]
    return lines


def validate_report(root: Path, meeting_path: Path, report_name: str = "report_auto.md") -> dict[str, Any]:
    report_path = meeting_path / report_name
    report = report_path.read_text(encoding="utf-8-sig") if report_path.is_file() else ""
    errors: list[str] = []
    if not report:
        errors.append(f"missing or empty report: {report_name}")
    headings = LIGHT_HEADINGS if profile_summary(meeting_path)["profile"] == "light" else REQUIRED_HEADINGS
    errors.extend(f"missing heading: {h}" for h in headings if h not in report)
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
        "required_headings": len(headings),
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
