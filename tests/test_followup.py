"""follow-up: сроки словами, стыки полей, люди вне встречи, принятые решения, пометка и сообщение другой стороне.

Подхватывается tests/run_tests.py автоматически. Основа — синтетическая встреча из фикстуры
(копия во временный каталог), артефакты подменяются в самих тестах — как в test_report.py.
Протокол — внутренний документ (всё, с пометками); сообщение — принятые решения (по статусному префиксу
current_state), действующие обязательства не ниже probable, формулировки открытых решений и вопросов,
следующий шаг.
"""

from __future__ import annotations

import json
import re

from run_tests import MEETING, WorkspaceTestCase, read_json, run

from na_engine.common import read_yaml, write_yaml
from test_report import FREE_TEXT, OPEN_STATES, TAKEN_STATES  # те же формулировки, что для «Требуемого решения»

OUTSIDER = "p-nordtech-ceo"
OUTSIDER_ROLE = "Генеральный директор Нордтеха, утверждает гарантийные условия (со слов Данилова). На встрече не присутствовал."
OUTSIDER_LABEL = "Генеральный директор Нордтеха, утверждает гарантийные условия (со слов Данилова) (не участник встречи)"
UNKNOWN = "p-unknown-director"
ZONING = "c-2026-10-28-polaris-zoning"
ESTIMATE = "c-2026-10-28-nordtech-estimate"
WARRANTY = "c-2026-10-28-nordtech-warranty-terms"
ZONING_WHAT = "передать Нордтеху схему зонирования распределительного склада — условие уточнённой сметы"


def section(text: str, start: str, end: str | None = None) -> str:
    """Текст от заголовка start до end (первого после start) или до конца."""
    head = text.index(start)
    return text[head:text.index(end, head)] if end else text[head:]


def decision(state: str, title: str, owner: str | None = "p-zaytseva-polaris") -> dict:
    return {
        "topic": "wms-payment-terms", "decision": title, "options": [], "current_state": state,
        "final_decision_maker": "p-krylova-polaris", "advisory_participants": [], "blocking_rights": None,
        "proposal_owner": owner, "proposal_owner_source": None, "deadline": None, "deadline_source": None,
        "evidence": [{"quote": "Решение по схеме оплаты, соответственно, примем до десятого ноября, после сметы.", "timestamp": "00:08:41"}],
        "confidence": "probable",
    }


class FollowupTestCase(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.prepare_valid_meeting()

    def artifact(self, name: str) -> dict:
        return read_json(self.meeting / "analysis" / name)

    def save(self, name: str, data: dict) -> None:
        (self.meeting / "analysis" / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def commitments(self) -> tuple[dict, dict[str, dict]]:
        data = self.artifact("07_commitments.json")
        return data, {item["id"]: item for item in data["commitments"]}

    def followup(self) -> tuple[str, str, dict]:
        completed = run(self.root, "followup", "--meeting", MEETING)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        protocol = (self.meeting / "follow_up_protocol.md").read_text(encoding="utf-8")
        message = (self.meeting / "follow_up_message.md").read_text(encoding="utf-8")
        bundle = read_json(self.meeting / "follow_up.json")
        self.assertEqual(bundle["delivery_status"], "draft_not_sent")
        return protocol, message, bundle

    @staticmethod
    def actions(protocol: str) -> dict[str, str]:
        """Строки таблицы «Действия» по тексту действия (третий столбец)."""
        table = section(protocol, "## Действия", "## Открытые вопросы")
        return {line.split(" | ")[2]: line for line in table.splitlines() if line.startswith("| ") and not line.startswith(("| Статус", "|---"))}

    @staticmethod
    def message_line(message: str, needle: str) -> str:
        return next(line for line in message.splitlines() if needle in line)


class DeadlineTests(FollowupTestCase):
    """1. deadline: null — срок словами «… (даты нет)» в протоколе и в сообщении, а не «—»/«срок не определён»."""

    def test_verbatim_deadline_in_protocol_and_message(self) -> None:
        protocol, message, _ = self.followup()
        rows = self.actions(protocol)
        self.assertEqual(rows[ZONING_WHAT].split(" | ")[3], "до пятницы (даты нет)")
        self.assertEqual(rows["подготовить условия гарантии на оборудование к следующей встрече"].split(" | ")[3], "к следующей встрече (даты нет)")
        self.assertEqual(rows["прислать уточнённую смету по трём складам"].split(" | ")[3], "2026-11-05")
        # ни даты, ни срока словами — прочерк, как раньше
        self.assertEqual(rows["запросить у прежнего подрядчика сохранившуюся документацию по API учётной системы"].split(" | ")[3], "—")

        self.assertIn(f"- Марина Зайцева — {ZONING_WHAT}; срок до пятницы (даты нет); статус: открыто.", message)
        self.assertIn("- Олег Данилов — прислать уточнённую смету по трём складам; срок 2026-11-05; статус: открыто.", message)
        self.assertIn("документацию по API учётной системы; срок не определён; статус: открыто.", message)


class JoinTests(FollowupTestCase):
    """2. Стыки полей: нет «..» и «.;» — clause/sentence из report.py."""

    def test_no_double_period_or_period_semicolon(self) -> None:
        decisions = self.artifact("09_decision_authority.json")
        decisions["decision_areas"][0]["decision"] = "Схема оплаты по этапам проекта."
        decisions["decision_areas"][0]["current_state"] = "Принято: оплата по этапам, оформляется допсоглашением."
        self.save("09_decision_authority.json", decisions)
        data, items = self.commitments()
        items[WARRANTY]["what"] += "."
        data["open_questions"][0]["question"] = "Условия гарантии на оборудование."
        self.save("07_commitments.json", data)
        protocol, message, _ = self.followup()

        self.assertIn("Принятые решения:\n- Схема оплаты по этапам проекта — принято: оплата по этапам, оформляется допсоглашением.\n", message)
        self.assertIn("- Олег Данилов — подготовить условия гарантии на оборудование к следующей встрече; срок к следующей встрече (даты нет);", message)
        self.assertIn("- Условия гарантии на оборудование.\n", message)
        self.assertIn("- Условия гарантии на оборудование — «", protocol)
        for text in (protocol, message):
            self.assertNotIn(".;", text)
            self.assertIsNone(re.search(r"(?<!\.)\.\.(?!\.)", text))


class PeopleTests(FollowupTestCase):
    """3. ЛПР, владелец шага, исполнитель и владелец риска вне участников — роль из 09 или `id` с пометкой."""

    def test_outsiders_are_labelled(self) -> None:
        decisions = self.artifact("09_decision_authority.json")
        area = decisions["decision_areas"][0]
        area["final_decision_maker"] = OUTSIDER
        area["proposal_owner"] = UNKNOWN
        decisions["authority_observations"].append({
            "person_id": OUTSIDER, "observed_role": OUTSIDER_ROLE, "formal_authority_in_decision": "Утверждает гарантию.",
            "evidence": [{"quote": "окончательное решение по схеме оплаты я приму после получения уточнённой сметы", "timestamp": "00:07:47"}],
            "confidence": "possible",
        })
        self.save("09_decision_authority.json", decisions)
        data, items = self.commitments()
        items[WARRANTY]["who"] = OUTSIDER
        items[ESTIMATE]["who"] = UNKNOWN
        self.save("07_commitments.json", data)
        risks = self.artifact("08_risks.json")
        risks["risks"][0]["mitigation"]["owner"] = UNKNOWN
        self.save("08_risks.json", risks)
        protocol, message, bundle = self.followup()

        unknown = f"`{UNKNOWN}` (не участник встречи)"
        decision_row = next(line for line in section(protocol, "## Решения и состояние", "## Действия").splitlines() if line.startswith("| Схема оплаты"))
        self.assertEqual(decision_row.split(" | ")[2:4], [OUTSIDER_LABEL, unknown])
        rows = self.actions(protocol)
        self.assertTrue(rows["подготовить условия гарантии на оборудование к следующей встрече"].startswith(f"| open | {OUTSIDER_LABEL} |"))
        self.assertTrue(rows["прислать уточнённую смету по трём складам"].startswith(f"| open | {unknown} |"))
        self.assertIn(f"| {unknown} |", section(protocol, "## Риски", "## Требует подтверждения"))

        # сообщение: роль из 09; без наблюдения — без id вовсе
        self.assertIn(f"- {OUTSIDER_LABEL} — подготовить условия гарантии на оборудование к следующей встрече;", message)
        self.assertIn("- не участник встречи, имя не названо — прислать уточнённую смету по трём складам; срок 2026-11-05;", message)
        self.assertNotIn(UNKNOWN, message)
        self.assertNotIn(OUTSIDER, message)
        # в протоколе сырой id не печатается без обратных кавычек
        for raw in (OUTSIDER, UNKNOWN):
            self.assertIsNone(re.search(rf"(?<![`\w-]){re.escape(raw)}(?![`\w-])", protocol), raw)
        self.assertEqual([item["person_id"] for item in bundle["authority_observations"]], ["p-krylova-polaris", OUTSIDER])

    def test_free_text_instead_of_id_is_printed_as_is(self) -> None:
        """Та же report.person: «(не участник встречи)» — только для id; свободный текст — как есть и в протоколе,
        и в сообщении (не «не участник встречи, имя не названо»)."""
        decisions = self.artifact("09_decision_authority.json")
        decisions["decision_areas"][0]["final_decision_maker"] = "финансовый директор «Нордтеха», ФИО не названо"
        self.save("09_decision_authority.json", decisions)
        data, items = self.commitments()
        items[ESTIMATE]["who"] = FREE_TEXT
        self.save("07_commitments.json", data)
        risks = self.artifact("08_risks.json")
        risks["risks"][0]["mitigation"]["owner"] = "служба безопасности заказчика"
        self.save("08_risks.json", risks)
        protocol, message, _ = self.followup()

        decision_row = next(line for line in section(protocol, "## Решения и состояние", "## Действия").splitlines() if line.startswith("| Схема оплаты"))
        self.assertEqual(decision_row.split(" | ")[2], "финансовый директор «Нордтеха», ФИО не названо")
        self.assertTrue(self.actions(protocol)["прислать уточнённую смету по трём складам"].startswith(f"| open | {FREE_TEXT} |"))
        self.assertIn("| служба безопасности заказчика |", section(protocol, "## Риски", "## Требует подтверждения"))
        self.assertIn(f"- {FREE_TEXT} — прислать уточнённую смету по трём складам; срок 2026-11-05; статус: открыто.", message)
        for text in (protocol, message):
            self.assertNotIn("(не участник встречи)", text)
            self.assertNotIn("имя не названо", text)


class OpenDecisionTests(FollowupTestCase):
    """4. Принятое или открытое — только по статусному префиксу current_state (как «Требуемое решение» отчёта).
    В сообщении «Принятые решения» — только по префиксу и не ниже probable; всё остальное — формулировкой
    в «Остаются открытыми», без состояния. Протокол — все решения с состоянием."""

    @staticmethod
    def items(message: str, start: str, end: str) -> list[str]:
        return [line for line in section(message, start, end).splitlines() if line.startswith("- ")]

    def test_prefix_decides_what_reaches_the_other_side(self) -> None:
        data = self.artifact("09_decision_authority.json")
        data["decision_areas"] = (
            [decision(state, title) for title, state in TAKEN_STATES.items()]
            + [decision(state, title) for title, state in OPEN_STATES.items()]
            + [decision("", "Гарантийные условия", owner=None),
               dict(decision("Согласовано: пилот на одном складе", "Пилот на втором складе"), confidence="possible")]
        )
        self.save("09_decision_authority.json", data)
        protocol, message, _ = self.followup()

        self.assertEqual(self.items(message, "Принятые решения:", "По действиям:"), [
            "- Аванс — принято: аванс 20% до 1 декабря.",
            "- Пилот — решено: Пилот на одном складе с 1 декабря.",
            "- Монтаж — согласовано: монтаж в январе.",
            "- Бюджет проекта — утверждено: 4,2 млн руб. на первый этап.",
            "- Гарантия — принято: гарантия три года.",
        ])
        still_open = self.items(message, "Остаются открытыми:", "Следующий шаг:")
        expected = [f"- {title}." for title in OPEN_STATES] + ["- Гарантийные условия.", "- Пилот на втором складе."]
        self.assertEqual(still_open[:len(expected)], expected)
        # состояние открытых (в том числе «Согласовано только с юристами», «Решение принято, но …») — не в сообщении
        for state in OPEN_STATES.values():
            self.assertNotIn(state, message)
        # «Согласовано: …» с confidence ниже probable — не выдаётся за принятое, только формулировкой в открытых
        self.assertNotIn("согласовано: пилот на одном складе", message)
        # протокол показывает все решения карты с состоянием
        table = section(protocol, "## Решения и состояние", "## Действия")
        rows = [line for line in table.splitlines() if line.startswith("| ") and not line.startswith(("| Вопрос", "|---"))]
        self.assertEqual(len(rows), len(TAKEN_STATES) + len(OPEN_STATES) + 2)
        self.assertIn("| Текст договора | Согласовано только с юристами, ждём подписи |", table)

    def test_legacy_states_without_prefix_are_all_open(self) -> None:
        """Старые артефакты без статусного префикса: ни одно решение не уходит другой стороне как принятое."""
        data = self.artifact("09_decision_authority.json")
        data["decision_areas"] = [
            decision("принято, оформляется допсоглашением", "Срок проекта 17 недель"),
            decision("согласовано в разговоре", "Пилот на одном складе"),
        ]
        self.save("09_decision_authority.json", data)
        _, message, _ = self.followup()
        self.assertNotIn("Принятые решения:", message)
        self.assertEqual(self.items(message, "Остаются открытыми:", "Следующий шаг:")[:2], ["- Срок проекта 17 недель.", "- Пилот на одном складе."])
        self.assertNotIn("оформляется допсоглашением", message)

    def test_same_wording_in_decision_and_question_is_listed_once(self) -> None:
        data = self.artifact("09_decision_authority.json")
        data["decision_areas"] = [decision("Не решено: вернёмся после сметы", "Кто согласует окна разгрузки с магазинами")]
        self.save("09_decision_authority.json", data)
        commitments, _ = self.commitments()
        commitments["open_questions"][0]["question"] = "Кто согласует окна разгрузки с магазинами?"
        self.save("07_commitments.json", commitments)
        _, message, _ = self.followup()
        self.assertEqual(self.items(message, "Остаются открытыми:", "Следующий шаг:"), ["- Кто согласует окна разгрузки с магазинами."])

    def test_fixture_open_decision_is_listed_without_state(self) -> None:
        protocol, message, _ = self.followup()
        self.assertNotIn("Принятые решения:", message)
        self.assertIn("Остаются открытыми:\n- Схема оплаты по этапам проекта.\n", message)
        self.assertNotIn("отложено до получения уточнённой сметы", message)
        self.assertIn("| Схема оплаты по этапам проекта | отложено до получения уточнённой сметы |", protocol)


class CurrentAgreementsTests(FollowupTestCase):
    """5. Ниже probable — «(требует подтверждения)»; related_mentions и prior_position_speaker в follow-up не попадают."""

    def test_low_confidence_commitment_is_marked(self) -> None:
        data, items = self.commitments()
        items[ZONING]["confidence"] = "possible"
        items[ESTIMATE]["confidence"] = "probable"
        self.save("07_commitments.json", data)
        protocol, message, _ = self.followup()
        rows = self.actions(protocol)
        self.assertIn(f"{ZONING_WHAT} (требует подтверждения)", rows)
        self.assertIn("прислать уточнённую смету по трём складам", rows)  # probable — без пометки
        self.assertEqual(sum("(требует подтверждения)" in line for line in rows.values()), 1)
        # другой стороне неподтверждённое не показывается вовсе; probable — показывается
        self.assertNotIn("схему зонирования", message)
        self.assertNotIn("(требует подтверждения)", message)
        self.assertIn("- Олег Данилов — прислать уточнённую смету по трём складам; срок 2026-11-05; статус: открыто.", message)

    def test_related_mentions_and_prior_position_speaker_are_not_shown(self) -> None:
        data, items = self.commitments()
        items[ESTIMATE]["related_mentions"] = [
            {"quote": "Смету пришлём до первого ноября.", "timestamp": "00:05:55", "note": "прежняя версия"},
            {"quote": "Смета до пятого ноября.", "timestamp": "00:09:00"},
        ]
        self.save("07_commitments.json", data)
        power = self.artifact("05_power.json")
        concession = power["concessions"][0]
        concession["prior_position_speaker"] = "p-krylova-polaris"
        self.save("05_power.json", power)
        protocol, message, bundle = self.followup()
        for text in (protocol, message):
            for needle in ("связанные реплики", "Смету пришлём до первого ноября", "00:05:55", "00:09:00", "прежняя версия",
                           concession["prior_position_quote"], "после позиции"):
                self.assertNotIn(needle, text)
        estimate = next(item for item in bundle["commitments"] if item["id"] == ESTIMATE)
        self.assertNotIn("related_mentions", estimate)
        self.assertEqual(estimate["what"], "прислать уточнённую смету по трём складам")
        # в самом артефакте разбора поле осталось — его читает отчёт
        self.assertIn("related_mentions", self.commitments()[1][ESTIMATE])


class PublicMessageTests(FollowupTestCase):
    """Сообщение другой стороне без внутреннего: очереди проверки, рисков, рекомендаций аналитика, id,
    неподтверждённых обязательств; в протоколе (внутреннем документе) всё это есть."""

    RISK_ID = "risk-2026-10-28-api-docs-delay"
    DIRECTOR = "nordtech-commercial-director"

    def test_internal_material_stays_in_protocol(self) -> None:
        data, items = self.commitments()
        items[ZONING]["confidence"] = "speculative"
        data["open_questions"][0]["question"] = (
            f"Какие условия гарантии на оборудование войдут в договор ({self.DIRECTOR}, 00:11:46)? "
            f"Решает {self.DIRECTOR}; см. {self.RISK_ID} и C1."
        )
        self.save("07_commitments.json", data)
        risks = self.artifact("08_risks.json")
        risks["risks"][0]["id"] = self.RISK_ID
        risks["risks"][0]["mitigation"]["planned_actions"] = "Рекомендация аналитика: до подписания запросить у Нордтеха план без документации по API."
        self.save("08_risks.json", risks)
        decisions = self.artifact("09_decision_authority.json")
        decisions["decision_areas"][0]["final_decision_maker"] = self.DIRECTOR
        self.save("09_decision_authority.json", decisions)
        review = read_yaml(self.meeting / "review.yaml")
        review["open_review_items"] = [{"question": f"ЛПР по гарантии — {self.DIRECTOR}; владелец риска {self.RISK_ID} не назван — назначить / не знаю"}]
        write_yaml(self.meeting / "review.yaml", review)
        protocol, message, bundle = self.followup()

        review_questions = [entry["question"] for entry in bundle["review_items"]]
        self.assertTrue(any(question.startswith("Проверить вывод с confidence=") for question in review_questions), review_questions)
        self.assertTrue(any(self.RISK_ID in question for question in review_questions), review_questions)
        internal = [
            *review_questions,
            "Проверить вывод с confidence",
            "Рекомендация аналитика",
            "отсутствия документации по API",  # описание риска
            ZONING_WHAT,
            "## Требует подтверждения человека",
        ]
        # протокол — внутренний: всё на месте
        for needle in internal:
            self.assertIn(needle, protocol)
        self.assertIn(f"{ZONING_WHAT} (требует подтверждения)", protocol)
        self.assertIn(f"`{self.DIRECTOR}` (не участник встречи)", protocol)

        # сообщение другой стороне — без внутреннего
        for needle in [*internal, "Нужно отдельно подтвердить", "Риски, которые остаются активными", "(требует подтверждения)", "Владелец:"]:
            self.assertNotIn(needle, message)
        self.assertIsNone(re.search(r"(?<![\w-])risk-[\w-]+", message))
        self.assertIsNone(re.search(r"[\w-]+-director(?![\w-])", message))
        self.assertIsNone(re.search(r"\d{2}:\d{2}:\d{2}", message))
        # открытое решение и открытый вопрос — только формулировка, без пометок разбора и id
        self.assertIn("Остаются открытыми:\n- Схема оплаты по этапам проекта.\n- Какие условия гарантии на оборудование войдут в договор?\n", message)
        # что остаётся: действующие обязательства обеих сторон со сроками и следующий шаг
        self.assertIn("- Олег Данилов — прислать уточнённую смету по трём складам; срок 2026-11-05; статус: открыто.", message)
        self.assertIn("- Анна Крылова — принять решение по схеме оплаты после получения уточнённой сметы; срок 2026-11-10; статус: открыто.", message)
        self.assertIn("Следующий шаг: прошу подтвердить договорённости и сроки выше.", message)

    def test_finished_commitments_are_not_listed(self) -> None:
        data, items = self.commitments()
        items[ESTIMATE]["status"] = "done"
        items[WARRANTY]["status"] = "cancelled"
        self.save("07_commitments.json", data)
        protocol, message, _ = self.followup()
        self.assertNotIn("прислать уточнённую смету по трём складам", message)
        self.assertNotIn("подготовить условия гарантии на оборудование", message)
        rows = self.actions(protocol)
        self.assertTrue(rows["прислать уточнённую смету по трём складам"].startswith("| done |"))
        self.assertTrue(rows["подготовить условия гарантии на оборудование к следующей встрече"].startswith("| cancelled |"))


class LegacyArtifactsTests(FollowupTestCase):
    """Старые артефакты без новых полей (deadline_verbatim, related_mentions, люди вне встречи) — как прежде."""

    def test_without_new_fields(self) -> None:
        data, items = self.commitments()
        for item in items.values():
            item["deadline_verbatim"] = None
            item.pop("related_mentions", None)
        self.save("07_commitments.json", data)
        protocol, message, _ = self.followup()
        for text in (protocol, message):
            for needle in ("(даты нет)", "(не участник встречи)", "(требует подтверждения)", "связанные реплики"):
                self.assertNotIn(needle, text)
        rows = self.actions(protocol)
        self.assertEqual(
            rows[ZONING_WHAT],
            f"| open | Марина Зайцева | {ZONING_WHAT} | — | «Схему зонирования мы передадим до пятницы, я проконтролирую логистику.» (`00:08:32`) |",
        )
        self.assertIn(f"- Марина Зайцева — {ZONING_WHAT}; срок не определён; статус: открыто.", message)
        self.assertIn("| Схема оплаты по этапам проекта | отложено до получения уточнённой сметы | Анна Крылова | Марина Зайцева | 2026-11-10 |", protocol)
