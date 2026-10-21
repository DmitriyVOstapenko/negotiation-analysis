"""report_auto.md: требуемое решение, исключённый фрагмент, сроки словами, люди вне встречи, первый экран по уверенности,
статус карты спикеров.

Подхватывается tests/run_tests.py автоматически. Основа — синтетическая встреча из фикстуры
(копия во временный каталог), артефакты подменяются в самих тестах.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from run_tests import FIXTURE, MEETING, WorkspaceTestCase, read_json, run

from na_engine.common import read_yaml, write_yaml
from na_engine.report import clause, decision_status, decision_taken, person, sentence, speaker_map_label

OUTSIDER = "p-nordtech-ceo"
OUTSIDER_ROLE = "Генеральный директор Нордтеха, утверждает гарантийные условия (со слов Данилова). На встрече не присутствовал."
UNKNOWN = "p-unknown-director"
# свободный текст вместо id (сценарий без разметки говорящих)
FREE_TEXT = "не установлен: сторона «Береста Ритейл» (Н. Гребнева или Ю. Абрамова)"


def section(text: str, start: str, end: str) -> str:
    """Текст между заголовками start и end (end — первый после start)."""
    head = text.index(start)
    return text[head:text.index(end, head)]


def decision(state: str, title: str, maker: str | None = "p-krylova-polaris") -> dict:
    return {
        "topic": "wms-payment-terms", "decision": title, "options": [], "current_state": state,
        "final_decision_maker": maker, "advisory_participants": [], "blocking_rights": None,
        "proposal_owner": None, "proposal_owner_source": None, "deadline": None, "deadline_source": None,
        "evidence": [{"quote": "Решение по схеме оплаты, соответственно, примем до десятого ноября, после сметы.", "timestamp": "00:08:41"}],
        "confidence": "probable",
    }


class ReportTestCase(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.prepare_valid_meeting()

    def artifact(self, name: str) -> dict:
        return read_json(self.meeting / "analysis" / name)

    def save(self, name: str, data: dict) -> None:
        (self.meeting / "analysis" / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def report(self) -> str:
        completed = run(self.root, "report", "--meeting", MEETING)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return (self.meeting / "report_auto.md").read_text(encoding="utf-8")

    def required_decision(self, text: str) -> str:
        return section(text, "### Требуемое решение", "### Ключевые факты")


# Формулировки current_state из независимой проверки (общие с test_followup.py): принято — только по
# статусному префиксу «Принято/Решено/Согласовано/Утверждено» + «:», «.» или «—»; остальное — открытое.
OPEN_STATES = {
    "Цена обслуживания": "Договорённости по цене нет: решили вернуться к вопросу после сметы",
    "Отсрочка платежа": "Договорённость не достигнута",
    "Схема оплаты": "Решили вернуться к вопросу после получения сметы",
    "Текст договора": "Согласовано только с юристами, ждём подписи",
    "Бюджет на сопровождение": "Решение не было принято: ждём утверждения бюджета",
    "Скидка на лицензии": "Решение принято, но требует утверждения СД",
    "Рамочное соглашение": "Подписан только протокол намерений",
    "Периодичность ТО": "Принято обеими сторонами в разговоре, в договоре не оформлено",
    "Штраф за опоздание": "Не решено: поставщик вернётся после ответа по цене",
    "Перенос запуска": "Отложено: до закрытия квартала",
    "Пилот на третьем складе": "Отменено: склад закрывается",
}
TAKEN_STATES = {
    "Аванс": "Принято: аванс 20% до 1 декабря",
    "Пилот": "Решено. Пилот на одном складе с 1 декабря",
    "Монтаж": "Согласовано — монтаж в январе",
    "Бюджет проекта": "Утверждено: 4,2 млн руб. на первый этап",
    "Гарантия": "  ПРИНЯТО: гарантия три года",
}


class RequiredDecisionTests(ReportTestCase):
    """D1: «Требуемое решение» — все области 09; принятое — только по статусному префиксу current_state,
    после открытых и с пометкой «(записано как принятое)»; без эвристик по тексту."""

    def test_status_prefix_only(self) -> None:
        for title, state in OPEN_STATES.items():
            self.assertFalse(decision_taken(state), title)
            self.assertEqual(decision_status(state), (None, state.strip()))
        for title, state in TAKEN_STATES.items():
            self.assertTrue(decision_taken(state), title)
        self.assertEqual(decision_status("Принято: аванс 20% до 1 декабря"), ("принято", "аванс 20% до 1 декабря"))
        self.assertEqual(decision_status("Согласовано — монтаж в январе"), ("согласовано", "монтаж в январе"))
        self.assertEqual(decision_status("Решено."), ("решено", ""))
        for state in (None, "", "принято, оформляется допсоглашением", "Принятое решение: аванс", "Решено частично: аванс"):
            self.assertFalse(decision_taken(state), state)

    def test_all_areas_shown_open_first_then_recorded_as_taken(self) -> None:
        data = self.artifact("09_decision_authority.json")
        # вперемешку: порядок в отчёте — сначала открытые, затем принятые
        states = list(TAKEN_STATES.items())[:2] + list(OPEN_STATES.items()) + list(TAKEN_STATES.items())[2:]
        data["decision_areas"] = [decision(state, title) for title, state in states] + [decision("", "Гарантийные условия", maker=UNKNOWN)]
        self.save("09_decision_authority.json", data)
        text = self.report()
        block = self.required_decision(text)
        items = [line for line in block.splitlines() if line.startswith("- **")]
        self.assertEqual(len(items), len(OPEN_STATES) + len(TAKEN_STATES) + 1)
        for title in [*OPEN_STATES, "Гарантийные условия"]:
            line = next(item for item in items if item.startswith(f"- **{title}** — состояние:"))
            self.assertLess(items.index(line), len(OPEN_STATES) + 1, title)
        for title in TAKEN_STATES:
            line = next(item for item in items if item.startswith(f"- **{title}** (записано как принятое) — состояние:"))
            self.assertGreaterEqual(items.index(line), len(OPEN_STATES) + 1, title)
        self.assertEqual(sum("(записано как принятое)" in item for item in items), len(TAKEN_STATES))
        self.assertNotIn("Открытых решений нет", block)
        # пустое состояние и ЛПР вне встречи: решение показано, id — не сырым
        self.assertIn(f"состояние: не указано; ЛПР: `{UNKNOWN}` (не участник встречи)", block)
        self.assertNotIn("срок: срок", block)
        self.assertIn("- Не закрыто: Условия гарантии на оборудование не обсуждены.", block)

    def test_all_taken_says_so_and_still_lists_them(self) -> None:
        data = self.artifact("09_decision_authority.json")
        data["decision_areas"] = [decision("Принято: оформляется допсоглашением", "Срок проекта 17 недель")]
        data["unresolved"] = []
        self.save("09_decision_authority.json", data)
        block = self.required_decision(self.report())
        self.assertIn("- Открытых решений нет: все решения карты записаны как принятые.\n", block)
        self.assertIn("- **Срок проекта 17 недель** (записано как принятое) — состояние: Принято: оформляется допсоглашением;", block)
        self.assertLess(block.index("Открытых решений нет"), block.index("Срок проекта 17 недель"))

    def test_legacy_states_without_prefix_are_open(self) -> None:
        """Старые артефакты: «принято, оформляется …» без статусного префикса — открытое, строки «нет открытых» нет."""
        data = self.artifact("09_decision_authority.json")
        data["decision_areas"] = [decision("принято, оформляется допсоглашением", "Срок проекта 17 недель")]
        data["unresolved"] = []
        self.save("09_decision_authority.json", data)
        block = self.required_decision(self.report())
        self.assertIn("- **Срок проекта 17 недель** — состояние: принято, оформляется допсоглашением;", block)
        self.assertNotIn("Открытых решений нет", block)
        self.assertNotIn("записано как принятое", block)


class ExcludedRangeTests(ReportTestCase):
    """Исключённый фрагмент записи: «с from до to»; to: null или нет поля — «до конца записи»."""

    def exclusion(self, value: dict | None, scope: dict | None = None) -> str:
        extraction = self.artifact("01_extraction.json")
        extraction["excluded_source_range"] = value
        self.save("01_extraction.json", extraction)
        if scope is not None:
            review = read_yaml(self.meeting / "review.yaml")
            review["source_scope"] = scope
            write_yaml(self.meeting / "review.yaml", review)
        header = section(self.report(), "# Автоматический отчёт", "## Один экран")
        return next((line for line in header.splitlines() if line.startswith("**Исключено")), "")

    def test_from_and_to(self) -> None:
        line = self.exclusion({"from": "00:10:05", "to": "00:12:40", "reason": "посторонний разговор после перерыва."})
        self.assertEqual(line, "**Исключено из анализа:** с `00:10:05` до `00:12:40` — посторонний разговор после перерыва.")

    def test_to_null_and_missing(self) -> None:
        line = self.exclusion({"from": "00:10:05", "to": None, "reason": "встреча закончилась"})
        self.assertEqual(line, "**Исключено из анализа:** с `00:10:05` до конца записи — встреча закончилась.")
        # старый артефакт без поля to и review.yaml без excluded_until — не падает, «до конца записи»
        self.assertIn("до конца записи", self.exclusion({"from": "00:10:05", "reason": "встреча закончилась"}))

    def test_end_from_review_scope_for_legacy_extraction(self) -> None:
        scope = {"status": "confirmed", "analyzed_from": None, "analyzed_until": None, "excluded_from": "00:10:05",
                 "excluded_until": "00:11:00", "exclusion_reason": "встреча закончилась", "source": "user_confirmation"}
        line = self.exclusion({"from": "00:10:05", "reason": "встреча закончилась"}, scope)
        self.assertIn("с `00:10:05` до `00:11:00`", line)

    def test_no_exclusion_no_line(self) -> None:
        self.assertEqual(self.exclusion(None), "")


class CommitmentTableTests(ReportTestCase):
    """D2: срок словами при deadline: null; «кто → кому»."""

    def test_verbatim_deadline_is_shown_when_date_is_null(self) -> None:
        text = self.report()
        table = section(text, "## Обязательства: актуальное состояние", "## Наблюдённые тактики")
        self.assertIn("| Кто → кому | Действие | Исходный срок |", table)
        rows = {line.split(" | ")[1]: line for line in table.splitlines() if line.startswith("| ") and " → " in line.split(" | ")[0]}
        zoning = rows["передать Нордтеху схему зонирования распределительного склада — условие уточнённой сметы"]
        self.assertTrue(zoning.startswith("| Марина Зайцева → Олег Данилов |"), zoning)
        self.assertIn("| до пятницы (даты нет) |", zoning)
        self.assertIn("| к следующей встрече (даты нет) |", rows["подготовить условия гарантии на оборудование к следующей встрече"])
        self.assertIn("| 2026-11-05 |", rows["прислать уточнённую смету по трём складам"])
        # ни даты, ни срока словами — прочерк
        api_docs = rows["запросить у прежнего подрядчика сохранившуюся документацию по API учётной системы"]
        self.assertEqual(api_docs.split(" | ")[2], "—")
        # без related_mentions строк-продолжений нет (старые артефакты печатаются как раньше)
        self.assertNotIn("| ↳ |", table)

    def test_related_mentions_line_under_commitment(self) -> None:
        commitments = self.artifact("07_commitments.json")
        estimate = next(c for c in commitments["commitments"] if c["id"] == "c-2026-10-28-nordtech-estimate")
        estimate["related_mentions"] = [
            {"quote": "Схему зонирования пришлём до пятницы.", "timestamp": "00:03:10", "note": "предложение."},
            {"quote": "Смета до пятого ноября.", "timestamp": "00:09:00"},
        ]
        self.save("07_commitments.json", commitments)
        text = self.report()
        lines = section(text, "## Обязательства: актуальное состояние", "## Наблюдённые тактики").splitlines()
        row = next(i for i, line in enumerate(lines) if "| прислать уточнённую смету по трём складам |" in line)
        self.assertEqual(lines[row + 1], "| ↳ | связанные реплики: 00:03:10 (предложение), 00:09:00 |  |  |  |  |  |")
        self.assertEqual(sum(1 for line in lines if line.startswith("| ↳ |")), 1)
        self.assertNotIn("связанные реплики", section(text, "## Один экран", "## Результат"))
        self.assertTrue(read_json(self.meeting / "analysis" / "report_auto_validation.json")["valid"])


class ConcessionTests(ReportTestCase):
    """05.concessions[].prior_position_speaker: чья была прежняя позиция, если не говорящего."""

    def test_prior_position_speaker(self) -> None:
        power = self.artifact("05_power.json")
        own = power["concessions"][0]
        by_colleague = dict(own, prior_position_speaker="p-krylova-polaris")
        same_person = dict(own, prior_position_speaker=own["speaker"])
        by_outsider = dict(own, prior_position_speaker=UNKNOWN)
        power["concessions"] = [own, by_colleague, same_person, by_outsider]
        self.save("05_power.json", power)
        lines = section(self.report(), "Уступки:", "## Эмоциональная динамика").splitlines()
        items = [line for line in lines if line.startswith("- ")]
        prior = f"«{own['prior_position_quote']}» (`{own['prior_position_timestamp']}`)."
        self.assertEqual(items[0], f"- Олег Данилов: «{own['quote']}» (`{own['timestamp']}`) после позиции {prior}")
        self.assertTrue(items[1].endswith(f"после позиции Анна Крылова: {prior}"), items[1])
        self.assertEqual(items[2], items[0])  # совпадает со speaker — как без поля
        self.assertTrue(items[3].endswith(f"после позиции `{UNKNOWN}` (не участник встречи): {prior}"), items[3])


class PeopleAndJoinsTests(ReportTestCase):
    """D3: люди вне списка участников — не сырым id; на стыках полей нет «.;» и «..»."""

    def test_outsiders_are_labelled_and_fields_are_joined_cleanly(self) -> None:
        commitments = self.artifact("07_commitments.json")
        warranty = next(c for c in commitments["commitments"] if c["id"] == "c-2026-10-28-nordtech-warranty-terms")
        warranty["to_whom"] = UNKNOWN
        warranty["what"] += "."
        self.save("07_commitments.json", commitments)
        risks = self.artifact("08_risks.json")
        risks["risks"][0]["mitigation"]["owner"] = OUTSIDER
        risks["risks"][0]["description"] = risks["risks"][0]["description"].rstrip(".")  # без точки — «Владелец» не слипается
        self.save("08_risks.json", risks)
        decisions = self.artifact("09_decision_authority.json")
        area = decisions["decision_areas"][0]
        area["final_decision_maker"] = OUTSIDER
        area["current_state"] = "отложено до получения уточнённой сметы."
        decisions["authority_observations"].append({
            "person_id": OUTSIDER, "observed_role": OUTSIDER_ROLE, "formal_authority_in_decision": "Утверждает гарантию.",
            "evidence": [{"quote": "окончательное решение по схеме оплаты я приму после получения уточнённой сметы", "timestamp": "00:07:47"}],
            "confidence": "possible",
        })
        decisions["unresolved"] = ["Условия гарантии на оборудование не обсуждены."]
        self.save("09_decision_authority.json", decisions)
        text = self.report()

        label = "Генеральный директор Нордтеха, утверждает гарантийные условия (со слов Данилова) (не участник встречи)"
        self.assertIn(f"ЛПР: {label}; срок: 2026-11-10", self.required_decision(text))
        self.assertIn(f"Владелец: {label};", section(text, "### Риски", "### Варианты"))
        self.assertIn(f"Олег Данилов → `{UNKNOWN}` (не участник встречи)", text)
        self.assertIn(f"- `{OUTSIDER}` (не участник встречи): Генеральный директор Нордтеха", text)
        # сырой id нигде не печатается без обратных кавычек
        for raw in (OUTSIDER, UNKNOWN):
            self.assertIsNone(re.search(rf"(?<![`\w-]){re.escape(raw)}(?![`\w-])", text), raw)
        self.assertNotIn(".;", text)
        self.assertIsNone(re.search(r"(?<!\.)\.\.(?!\.)", text))
        self.assertIn("состояние: отложено до получения уточнённой сметы; ЛПР", text)
        self.assertIn("- Не закрыто: Условия гарантии на оборудование не обсуждены.\n", text)
        self.assertTrue(read_json(self.meeting / "analysis" / "report_auto_validation.json")["valid"])

    def test_free_text_instead_of_id_is_printed_as_is(self) -> None:
        """Пометка «(не участник встречи)» — только для строки вида id; свободный текст — как есть."""
        names = {"p-krylova-polaris": "Анна Крылова"}
        self.assertEqual(person("p-krylova-polaris", names), "Анна Крылова")
        self.assertEqual(person(UNKNOWN, names), f"`{UNKNOWN}` (не участник встречи)")
        self.assertEqual(person(FREE_TEXT, names), FREE_TEXT)
        self.assertEqual(person("Финансовый директор перевозчика", names), "Финансовый директор перевозчика")
        self.assertEqual(person("P-Krylova", names), "P-Krylova")  # не id: заглавные

        tactics = self.artifact("02_tactics.json")
        tactics["voss"][0]["used_by"] = FREE_TEXT
        self.save("02_tactics.json", tactics)
        power = self.artifact("05_power.json")
        power["framing_moments"][0]["speaker"] = "не установлен: сторона не определена"
        self.save("05_power.json", power)
        risks = self.artifact("08_risks.json")
        risks["risks"][0]["mitigation"]["owner"] = "служба безопасности заказчика"
        self.save("08_risks.json", risks)
        decisions = self.artifact("09_decision_authority.json")
        decisions["decision_areas"][0]["final_decision_maker"] = "финансовый директор «Нордтеха», ФИО не названо"
        decisions["authority_observations"].append({
            "person_id": "финансовый директор «Нордтеха»", "observed_role": "Утверждает отсрочку (со слов Данилова).",
            "formal_authority_in_decision": None, "evidence": [], "confidence": "probable",
        })
        self.save("09_decision_authority.json", decisions)
        text = self.report()
        self.assertIn(f"- `voss:{tactics['voss'][0]['tactic_type']}` — {FREE_TEXT}: «", text)
        self.assertIn("- не установлен: сторона не определена: ", section(text, "Контроль рамки обсуждения:", "## Эмоциональная динамика"))
        self.assertIn("Владелец: служба безопасности заказчика;", section(text, "### Риски", "### Варианты"))
        self.assertIn("ЛПР: финансовый директор «Нордтеха», ФИО не названо;", self.required_decision(text))
        self.assertIn("- финансовый директор «Нордтеха»: Утверждает отсрочку", text)
        for free in (FREE_TEXT, "сторона не определена", "служба безопасности заказчика", "ФИО не названо", "«Нордтеха»"):
            self.assertIsNone(re.search(rf"{re.escape(free)}`? \(не участник встречи\)", text), free)

    def test_clause_keeps_abbreviations_and_sentence_ends_once(self) -> None:
        self.assertEqual(clause("отложено до сметы.;"), "отложено до сметы")
        self.assertEqual(clause("перенесён на 9 декабря 2026 г."), "перенесён на 9 декабря 2026 г.")
        self.assertEqual(clause("и так далее..."), "и так далее...")
        self.assertEqual(sentence("Риск без точки"), "Риск без точки.")
        self.assertEqual(sentence("Риск с точкой."), "Риск с точкой.")
        self.assertEqual(sentence("180 тыс. руб."), "180 тыс. руб.")


class FirstScreenConfidenceTests(ReportTestCase):
    """D4: на первом экране — certain/probable (сначала high); ниже probable — в «Рисках: актуальное состояние» с пометкой."""

    def test_possible_risk_is_not_on_first_screen(self) -> None:
        risks = self.artifact("08_risks.json")
        base = risks["risks"][0]
        medium_possible = dict(base, id="r-2026-10-28-possible-medium", severity="medium", confidence="possible",
                               description="Возможная задержка поставки сканеров из-за смены поставщика.")
        high_possible = dict(base, id="r-2026-10-28-possible-high", severity="high", confidence="possible",
                             description="Возможный отказ банка финансировать оборудование.")
        medium_certain = dict(base, id="r-2026-10-28-certain-medium", severity="medium", confidence="certain",
                              description="Смета без зонирования распределительного склада неточна.")
        risks["risks"] = [medium_possible, medium_certain, high_possible, base]
        self.save("08_risks.json", risks)
        text = self.report()
        screen = section(text, "### Риски", "### Варианты")
        self.assertNotIn("Возможная задержка поставки сканеров", screen)
        self.assertIn("Ещё 1 риск с уверенностью ниже probable — не на первом экране", screen)
        # high certain → high possible (с пометкой) → medium certain
        order = [screen.index(needle) for needle in ("отсутствия документации по API", "Возможный отказ банка", "Смета без зонирования")]
        self.assertEqual(order, sorted(order))
        self.assertIn("- **high** _(требует проверки: confidence `possible`)_ — Возможный отказ банка", screen)
        table = section(text, "## Риски: актуальное состояние", "## Протокол следующей встречи")
        self.assertIn("| medium | possible — требует проверки | open | Возможная задержка поставки сканеров", table)
        self.assertLess(table.index("Возможный отказ банка"), table.index("Смета без зонирования"))
        check = read_json(self.meeting / "analysis" / "report_auto_validation.json")
        self.assertTrue(check["valid"], check["errors"])
        self.assertEqual(check["risks_checked"], 4)

    def test_low_confidence_commitment_and_fact_on_first_screen(self) -> None:
        commitments = self.artifact("07_commitments.json")
        zoning = next(c for c in commitments["commitments"] if c["id"] == "c-2026-10-28-polaris-zoning")
        zoning.update(deadline="2026-10-30", confidence="possible")
        self.save("07_commitments.json", commitments)
        extraction = self.artifact("01_extraction.json")
        extraction["key_facts"][1]["confidence"] = "speculative"
        self.save("01_extraction.json", extraction)
        text = self.report()
        facts = section(text, "### Ключевые факты", "### Риски")
        self.assertNotIn(extraction["key_facts"][1]["fact"].rstrip("."), facts)
        self.assertIn(extraction["key_facts"][1]["fact"].rstrip("."), section(text, "## Ключевые факты", "## Обязательства"))
        self.assertIn("из них требуют подтверждения (confidence ниже probable): 1", facts)
        self.assertIn("ближайший срок — 2026-10-30: Марина Зайцева — передать Нордтеху схему зонирования распределительного склада — условие уточнённой сметы (требует подтверждения).", facts)
        step = section(text, "### Следующий шаг", "## Результат")
        self.assertIn("условие уточнённой сметы (требует подтверждения) (срок 2026-10-30)", step)
        self.assertIn("open; confidence `possible` — требует подтверждения", text)


class SpeakerMapStatusTests(WorkspaceTestCase):
    """D5: «карта спикеров подтверждена» — только если сопоставлены все метки."""

    def test_partial_and_proposed_map(self) -> None:
        self.register()
        self.assertEqual(run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "yes", "--masking", "none", "--sensitivity", "internal", "--routes", "local_only", "--retain-source", "yes").returncode, 0)
        self.assertEqual(run(self.root, "speakers", "set", "--meeting", MEETING, "--map", "Speaker 1=Анна Крылова;Speaker 2=Олег Данилов").returncode, 0)
        review = read_yaml(self.meeting / "review.yaml")
        self.assertEqual(review["speaker_mapping"]["status"], "confirmed")  # движок ставит confirmed и на частичную карту
        for name in ("01_extraction.json", "02_tactics.json", "03_manipulations.json", "04_arguments.json", "05_power.json", "06_emotions.json",
                     "07_commitments.json", "08_risks.json", "09_decision_authority.json", "10_argument_map.json", "11_meeting_effectiveness.json"):
            (self.meeting / "analysis" / name).write_bytes((FIXTURE / "analysis" / name).read_bytes())
        run(self.root, "validate", "--meeting", MEETING)
        self.assertEqual(run(self.root, "report", "--meeting", MEETING).returncode, 0)
        text = (self.meeting / "report_auto.md").read_text(encoding="utf-8")
        data_line = text.splitlines()[2]
        self.assertIn("карта спикеров подтверждена частично (без сопоставления: Speaker 3)", data_line)
        self.assertNotIn("подтверждена пользователем", data_line)
        self.assertIn("карта спикеров: подтверждена частично (без сопоставления: Speaker 3)", section(text, "### Качество источника", "---"))

        review["speaker_mapping"]["status"] = "proposed"
        write_yaml(self.meeting / "review.yaml", review)
        self.assertEqual(run(self.root, "report", "--meeting", MEETING).returncode, 0)
        self.assertIn("карта спикеров предложена, не подтверждена", (self.meeting / "report_auto.md").read_text(encoding="utf-8").splitlines()[2])

    def test_label_function(self) -> None:
        full = {"speaker_mapping": {"status": "confirmed", "speakers": [{"label": "Speaker 1", "person_name": "Анна"}, {"label": "Speaker 2", "person_name": "Олег"}]}}
        self.assertEqual(speaker_map_label(full, ["Speaker 1", "Speaker 2"]), "подтверждена пользователем")
        self.assertEqual(speaker_map_label(full, ["Speaker 1", "Speaker 2", "Speaker 3"]), "подтверждена частично (без сопоставления: Speaker 3)")
        self.assertEqual(speaker_map_label({"speaker_mapping": {"status": "pending", "speakers": []}}, []), "не подтверждена")
        self.assertEqual(speaker_map_label({}, ["Speaker 1"]), "не подтверждена")
