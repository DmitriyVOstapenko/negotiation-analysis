"""Область K: дополнения контрактов анализа (обратно совместимые, необязательные поля).

- 07 `related_mentions` — связанные реплики обязательства: схема закрыта, цитаты проверяются по
  транскрипту и таймкоду, атрибуция к `who` не проверяется;
- 05 `concessions[].prior_position_speaker` — прежнюю позицию назвал не тот, кто уступил;
- 01 `excluded_source_range.to` — конец исключённого фрагмента;
- 01 `participants[].evidence` / `evidence_note` — как установлена личность.

Запускается из tests/run_tests.py (подхватывает tests/test_*.py).
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any, Callable

from run_tests import FIXTURE, MEETING, REPO, WorkspaceTestCase, payload, read_json, run

from na_engine.schema_runtime import load_registry, validate_contract  # run_tests добавил scripts/ в sys.path

REGISTRY = load_registry(REPO / "core" / "contracts" / "contracts.schema.json")

# Синтетическая встреча: Speaker 1 — Анна Крылова, Speaker 2 — Олег Данилов, Speaker 3 — Марина Зайцева.
KRYLOVA, DANILOV, ZAYTSEVA = "p-krylova-polaris", "p-danilov-nordtech", "p-zaytseva-polaris"
ESTIMATE_REQUEST = {"quote": "Нам нужна смета до пятого ноября, чтобы успеть согласовать бюджет внутри компании.", "timestamp": "00:08:08", "note": "запрос заказчика"}
ESTIMATE_SUMMARY = {"quote": "Первое: мы присылаем уточнённую смету по трём складам до пятого ноября.", "timestamp": "00:10:42", "note": "подтверждено в итогах"}
# Уступка Данилова (00:07:35); прежнюю позицию его стороны («пятьдесят процентов аванса») назвала Зайцева (00:06:37).
OTHER_SIDE_CONCESSION = {
    "speaker": DANILOV,
    "quote": "Если первый платёж идёт сразу после обследования, а не после внедрения, мы готовы обсуждать такую схему.",
    "timestamp": "00:07:35",
    "prior_position_quote": "Пятьдесят процентов аванса для нас слишком много при текущей неопределённости по интеграции.",
    "prior_position_timestamp": "00:06:37",
    "prior_position_speaker": ZAYTSEVA,
}


def fixture(name: str) -> dict[str, Any]:
    return read_json(FIXTURE / "analysis" / name)


def schema_errors(name: str, data: dict[str, Any]) -> list[dict[str, str]]:
    return validate_contract(data, REGISTRY, REGISTRY["x-file-contracts"][name])


class ContractSchemaTests(unittest.TestCase):
    def test_fixture_artifacts_remain_valid(self) -> None:
        # 1.1.0 добавила только необязательные поля: разбор 1.0.0 (фикстура без них) проходит как есть
        self.assertEqual(REGISTRY["x-contract-version"], "1.1.0")
        for name in REGISTRY["x-file-contracts"]:
            self.assertEqual(schema_errors(name, fixture(name)), [], name)

    def test_related_mentions_are_optional_and_closed(self) -> None:
        data = fixture("07_commitments.json")
        data["commitments"][0]["related_mentions"] = [ESTIMATE_REQUEST, {"quote": ESTIMATE_SUMMARY["quote"], "timestamp": "00:10:42"}]
        data["commitments"][1]["related_mentions"] = []
        data["commitments"][2]["related_mentions"] = [{**ESTIMATE_REQUEST, "note": None}]
        self.assertEqual(schema_errors("07_commitments.json", data), [])

        data["commitments"][0]["related_mentions"][1]["who"] = KRYLOVA
        errors = schema_errors("07_commitments.json", data)
        self.assertEqual([(item["path"], item["keyword"]) for item in errors], [("$.commitments[0].related_mentions[1].who", "additionalProperties")])

        data["commitments"][0]["related_mentions"] = [{"quote": ESTIMATE_REQUEST["quote"]}, {"quote": "", "timestamp": "8:08"}]
        keywords = {(item["path"], item["keyword"]) for item in schema_errors("07_commitments.json", data)}
        self.assertEqual(keywords, {
            ("$.commitments[0].related_mentions[0].timestamp", "required"),
            ("$.commitments[0].related_mentions[1].quote", "minLength"),
            ("$.commitments[0].related_mentions[1].timestamp", "pattern"),
        })

    def test_prior_position_speaker_is_optional_nullable_id(self) -> None:
        data = fixture("05_power.json")
        data["concessions"].append(copy.deepcopy(OTHER_SIDE_CONCESSION))
        self.assertEqual(schema_errors("05_power.json", data), [])
        data["concessions"][1]["prior_position_speaker"] = None
        self.assertEqual(schema_errors("05_power.json", data), [])
        data["concessions"][1]["prior_position_speaker"] = 7
        self.assertEqual([item["keyword"] for item in schema_errors("05_power.json", data)], ["type"])

    def test_excluded_source_range_accepts_end(self) -> None:
        data = fixture("01_extraction.json")
        for value in ({"from": "00:12:16", "to": "00:12:22", "reason": "прощание после окончания встречи"},
                      {"from": "12:16", "to": None, "reason": "до конца записи"},
                      {"from": "00:12:16", "reason": "до конца записи"}, None):
            data["excluded_source_range"] = value
            self.assertEqual(schema_errors("01_extraction.json", data), [], value)
        data["excluded_source_range"] = {"from": "00:12:16", "to": "до конца", "reason": "x"}
        self.assertEqual([item["keyword"] for item in schema_errors("01_extraction.json", data)], ["pattern"])
        data["excluded_source_range"] = {"from": "00:12:16", "until": "00:12:22", "reason": "x"}
        self.assertEqual([item["keyword"] for item in schema_errors("01_extraction.json", data)], ["additionalProperties"])

    def test_participant_identity_evidence_is_optional(self) -> None:
        data = fixture("01_extraction.json")
        data["participants"][2]["evidence"] = [{"quote": "Я по финансовой части, буду задавать вопросы по смете и графику платежей.", "timestamp": "00:00:31"}]
        data["participants"][2]["evidence_note"] = "представилась сама (роль); имя подтвердил пользователь"
        data["participants"][1]["evidence_note"] = None
        self.assertEqual(schema_errors("01_extraction.json", data), [])
        data["participants"][2]["evidence"][0]["speaker"] = ZAYTSEVA
        self.assertEqual([item["keyword"] for item in schema_errors("01_extraction.json", data)], ["additionalProperties"])


class ContractValidationTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.prepare_valid_meeting()
        self.baseline = self.validate()[1]

    def edit(self, name: str, mutate: Callable[[dict[str, Any]], None]) -> None:
        path = self.meeting / "analysis" / name
        data = read_json(path)
        mutate(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def validate(self) -> tuple[int, dict[str, Any]]:
        completed = run(self.root, "validate", "--meeting", MEETING)
        return completed.returncode, payload(completed)

    @staticmethod
    def check_for(report: dict[str, Any], filename: str, path: str) -> dict[str, Any]:
        return next(item for item in report["quote_checks"] if item["file"] == filename and item["path"] == path)

    def test_related_mentions_are_checked_without_attribution(self) -> None:
        # обязательство Данилова (00:08:18): запрос Крыловой и повтор в итогах — связанные реплики
        self.edit("07_commitments.json", lambda data: data["commitments"][0].update(related_mentions=[ESTIMATE_REQUEST, ESTIMATE_SUMMARY]))
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        stats, before = report["quote_stats"], self.baseline["quote_stats"]
        self.assertEqual(report["checked_quotes"], self.baseline["checked_quotes"] + 2)
        self.assertEqual(stats["exact"], before["exact"] + 2)
        self.assertEqual((stats["attribution_checked"], stats["attribution_mismatched"]), (before["attribution_checked"], 0))
        self.assertFalse([item for item in report["warnings"] if item.startswith("якорь обязательства") or "related_mentions" in item], report["warnings"])
        request = self.check_for(report, "07_commitments.json", "$.commitments[0].related_mentions[0].quote")
        self.assertEqual((request["level"], request["timestamp"], request["speaker_labels"], request["timestamp_check"]), ("exact", "00:08:08", ["Speaker 1"], "ok"))
        self.assertNotIn("attribution", request)

        # несуществующая цитата и таймкод на чужую реплику — те же ошибки, что у опоры
        def broken(data: dict[str, Any]) -> None:
            mentions = data["commitments"][0]["related_mentions"]
            mentions[0]["timestamp"] = "00:04:01"
            mentions[1]["quote"] = "Смету пришлём к первому ноября."

        self.edit("07_commitments.json", broken)
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("quote not found in transcript: 07_commitments.json $.commitments[0].related_mentions[1].quote", report["errors"])
        self.assertTrue(any(item.startswith("таймкод указывает на другую реплику: 07_commitments.json $.commitments[0].related_mentions[0].quote") for item in report["errors"]), report["errors"])

        # лишнее поле внутри элемента — нарушение схемы
        self.edit("07_commitments.json", lambda data: data["commitments"][0].update(related_mentions=[{**ESTIMATE_REQUEST, "speaker": KRYLOVA}]))
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertFalse(report["schema_valid"])
        self.assertTrue(any("$.commitments[0].related_mentions[0].speaker [additionalProperties]" in item for item in report["errors"]), report["errors"])

    def test_concession_prior_position_by_other_speaker(self) -> None:
        self.edit("05_power.json", lambda data: data["concessions"].append(copy.deepcopy(OTHER_SIDE_CONCESSION)))
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        prior = self.check_for(report, "05_power.json", "$.concessions[1].prior_position_quote")["attribution"]
        self.assertEqual((prior["key"], prior["claimed"], prior["mode"], prior["result"]), ("prior_position_speaker", ZAYTSEVA, "strict", "match"))
        own = self.check_for(report, "05_power.json", "$.concessions[1].quote")["attribution"]
        self.assertEqual((own["key"], own["claimed"], own["result"]), ("speaker", DANILOV, "match"))
        fixture_prior = self.check_for(report, "05_power.json", "$.concessions[0].prior_position_quote")["attribution"]
        self.assertEqual((fixture_prior["key"], fixture_prior["result"]), ("speaker", "match"))

        # нет поля или null — прежнее поведение: прежняя позиция сверяется со speaker
        for variant in ("absent", None):
            def mutate(data: dict[str, Any], variant: Any = variant) -> None:
                item = data["concessions"][1]
                if variant == "absent":
                    item.pop("prior_position_speaker", None)
                else:
                    item["prior_position_speaker"] = variant

            self.edit("05_power.json", mutate)
            code, report = self.validate()
            self.assertEqual(code, 1, variant)
            mismatches = [item for item in report["errors"] if item.startswith("атрибуция цитаты не совпадает с картой спикеров: 05_power.json $.concessions[1].prior_position_quote")]
            self.assertEqual(len(mismatches), 1, report["errors"])
            self.assertIn(f"speaker={DANILOV!r}", mismatches[0])
            self.assertIn("Марина Зайцева", mismatches[0])

        # заданный, но неверный говорящий прежней позиции — тоже ошибка
        self.edit("05_power.json", lambda data: data["concessions"][1].update(prior_position_speaker=KRYLOVA))
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertTrue(any(f"prior_position_speaker={KRYLOVA!r}" in item for item in report["errors"]), report["errors"])

    def test_excluded_source_range_end_is_valid_and_ordered(self) -> None:
        self.edit("01_extraction.json", lambda data: data.update(excluded_source_range={"from": "00:12:16", "to": "00:12:22", "reason": "прощание после окончания встречи"}))
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertTrue(report["schema_valid"])

        self.edit("01_extraction.json", lambda data: data["excluded_source_range"].update(to="00:12:00"))
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("excluded_source_range: конец 00:12:00 раньше начала 00:12:16 (01_extraction.json)", report["errors"])

    def test_participant_identity_evidence_is_checked_without_attribution(self) -> None:
        def mutate(data: dict[str, Any]) -> None:
            # самопредставление Зайцевой (Speaker 3) и, у Крыловой, реплика другого человека — атрибуции нет
            data["participants"][2].update(evidence=[{"quote": "Я по финансовой части, буду задавать вопросы по смете и графику платежей.", "timestamp": "00:00:31"}],
                                           evidence_note="представилась сама (роль); имя подтвердил пользователь")
            data["participants"][0].update(evidence=[{"quote": "Здесь нам нужна документация по API вашей учётной системы.", "timestamp": "00:02:52"}])

        self.edit("01_extraction.json", mutate)
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["checked_quotes"], self.baseline["checked_quotes"] + 2)
        self.assertEqual(report["quote_stats"]["attribution_checked"], self.baseline["quote_stats"]["attribution_checked"])
        self.assertNotIn("attribution", self.check_for(report, "01_extraction.json", "$.participants[0].evidence[0].quote"))

        self.edit("01_extraction.json", lambda data: data["participants"][2]["evidence"][0].update(quote="Меня зовут Марина Зайцева."))
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("quote not found in transcript: 01_extraction.json $.participants[2].evidence[0].quote", report["errors"])


class LongMeetingRangeTests(WorkspaceTestCase):
    """Связанные реплики и доказательства личности могут лежать вне своей части — предупреждение."""

    def write(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_related_and_identity_quotes_outside_part_are_warnings(self) -> None:
        self.prepare_valid_meeting()
        plan = payload(run(self.root, "parts", "plan", "--meeting", MEETING, "--max-chars", "4000"))
        self.assertEqual(len(plan["parts"]), 2)
        lines = (self.meeting / "transcript.txt").read_text(encoding="utf-8").splitlines()
        second = plan["parts"][1]
        second_text = "\n".join(lines[second["line_from"] - 1 : second["line_to"]])
        early = {"quote": "Мы запросим у прежнего подрядчика, что у него сохранилось. Я беру это на себя.", "timestamp": "00:04:01", "note": "прежняя версия"}
        self.assertNotIn(early["quote"], second_text)
        warranty = next(item for item in fixture("07_commitments.json")["commitments"] if item["source_timestamp"] == "00:11:56")
        self.assertIn(warranty["source_quote"], second_text)

        parts_dir = self.meeting / "analysis" / "parts"
        commitments = {"meeting_id": MEETING, "commitments": [{**warranty, "related_mentions": [early]}], "open_questions": []}
        self.write(parts_dir / "07_commitments.part-2.json", commitments)
        extraction = fixture("01_extraction.json")
        extraction["participants"][2]["evidence"] = [{"quote": "Я по финансовой части, буду задавать вопросы по смете и графику платежей.", "timestamp": "00:00:31"}]
        extraction.update(topics=[], key_facts=[])
        self.write(parts_dir / "01_extraction.part-2.json", extraction)

        result = run(self.root, "parts", "check", "--meeting", MEETING)
        files = {item["file"]: item for item in payload(result)["files"]}
        self.assertEqual(result.returncode, 0, json.dumps(files, ensure_ascii=False, indent=1))
        self.assertTrue(any("вне своей части" in item and "related_mentions[0].quote" in item for item in files["07_commitments.part-2.json"]["warnings"]))
        self.assertTrue(any("вне своей части" in item and "participants[2].evidence[0].quote" in item for item in files["01_extraction.part-2.json"]["warnings"]))

        # опора самого обязательства из чужой части — по-прежнему ошибка
        commitments["commitments"][0]["source_quote"], commitments["commitments"][0]["source_timestamp"] = early["quote"], early["timestamp"]
        self.write(parts_dir / "07_commitments.part-2.json", commitments)
        result = run(self.root, "parts", "check", "--meeting", MEETING, "--file", "07_commitments.part-2.json")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any("вне своей части" in error and error.endswith("source_quote") for item in payload(result)["files"] for error in item["errors"]))
