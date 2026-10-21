"""Область Q: строгая проверка цитат (уровни, склейки, атрибуция, таймкоды) и лёгкий профиль разбора.

Запускается из tests/run_tests.py (подхватывает tests/test_*.py); отдельно — python3 -m unittest из каталога tests.
"""

from __future__ import annotations

import json
import shutil
from typing import Any, Callable

from run_tests import ANALYSIS_FILES, FIXTURE, MEETING, SPEAKER_MAP, WorkspaceTestCase, payload, read_json, run

LIGHT_FILES = ("01_extraction.json", "07_commitments.json", "08_risks.json")


class QuoteTestCase(WorkspaceTestCase):
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

    def gate(self) -> None:
        completed = run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "yes", "--masking", "none", "--sensitivity", "internal", "--routes", "local_only", "--retain-source", "yes")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class StrictQuoteTests(QuoteTestCase):
    def test_fixture_quotes_are_exact_and_attributed(self) -> None:
        self.prepare_valid_meeting()
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        stats = report["quote_stats"]
        self.assertEqual((stats["checked"], stats["exact"], stats["normalized"], stats["ellipsis"]), (35, 35, 0, 0))
        self.assertEqual((stats["not_found"], stats["spliced"], stats["attribution_mismatched"]), (0, 0, 0))
        self.assertGreaterEqual(stats["attribution_matched"], 15)
        self.assertEqual(stats["timestamp_drift"] + stats["timestamp_other_reply"], 0)
        quality = report["source_quality"]
        self.assertTrue(quality["has_timestamps"])
        self.assertEqual(quality["speaker_labels"], ["Speaker 1", "Speaker 2", "Speaker 3"])
        self.assertEqual(quality["speaker_map_status"], "confirmed")
        self.assertEqual(report["timestamp_tolerance_sec"], 15)

        self.assertEqual(run(self.root, "report", "--meeting", MEETING).returncode, 0)
        lines = (self.meeting / "report_auto.md").read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[2].startswith("**Данные:**"), lines[:4])
        for needle in ("таймкоды есть", "разметка говорящих есть", "карта спикеров подтверждена", "буквально 35", "разбор полный"):
            self.assertIn(needle, lines[2])
        self.assertIn("### Качество источника", lines)

    def test_normalized_quote_is_accepted_and_flagged(self) -> None:
        self.prepare_valid_meeting()

        def mutate(data: dict[str, Any]) -> None:
            # регистр и пунктуация; тире вместо дефиса; «е» вместо «ё»
            data["key_facts"][0]["quote"] = "общая смета изменится не более чем на пять процентов и только в сторону уменьшения"
            data["key_facts"][2]["quote"] = "Если документация будет, семнадцать недель - реалистичный срок"
            data["key_facts"][4]["quote"] = "Тогда в уточненной смете поставим двадцать четыре лицензии, а запас вынесем в отдельную опцию."

        self.edit("01_extraction.json", mutate)
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["quote_stats"]["normalized"], 3)
        self.assertEqual(report["quote_stats"]["exact"], 32)
        flagged = self.check_for(report, "01_extraction.json", "$.key_facts[0].quote")
        self.assertEqual((flagged["level"], flagged["timestamp"], flagged["speaker_labels"]), ("normalized", "00:02:27", ["Speaker 2"]))

        self.assertEqual(run(self.root, "report", "--meeting", MEETING).returncode, 0)
        text = (self.meeting / "report_auto.md").read_text(encoding="utf-8")
        self.assertIn("после нормализации 3", text)
        self.assertIn("`01_extraction.json $.key_facts[0].quote` — после нормализации", text)

    def test_ellipsis_quote_is_found_by_parts(self) -> None:
        self.prepare_valid_meeting()

        def mutate(data: dict[str, Any]) -> None:
            data["key_facts"][0]["quote"] = "Да, примерно так. … только в сторону уменьшения."
            # части в двух соседних репликах одного говорящего (Speaker 2: 00:10:42 и 00:10:56)
            data["key_facts"][1]["quote"] = "Первое: мы присылаем уточнённую смету по трём складам... Четвёртое: мы готовы обсуждать схему тридцать, сорок, тридцать"
            data["key_facts"][1]["timestamp"] = "00:10:42"

        self.edit("01_extraction.json", mutate)
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["quote_stats"]["ellipsis"], 2)
        across = self.check_for(report, "01_extraction.json", "$.key_facts[1].quote")
        self.assertEqual((across["level"], across["segments"], across["speaker_labels"]), ("ellipsis", 2, ["Speaker 2"]))

        # пропуск не разрешает менять порядок частей
        self.edit("01_extraction.json", lambda data: data["key_facts"][0].update(quote="только в сторону уменьшения … Да, примерно так."))
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertTrue(any(item.startswith("quote not found in transcript: 01_extraction.json $.key_facts[0].quote") for item in report["errors"]))

    def test_splice_of_different_speakers_is_error(self) -> None:
        self.prepare_valid_meeting()
        # Speaker 2 (00:08:18) + Speaker 3 (00:08:32): с многоточием и без
        self.edit("07_commitments.json", lambda data: data["commitments"][0].update(source_quote="Я пришлю уточнённую смету по трём складам до пятого ноября … Схему зонирования мы передадим до пятницы"))
        self.edit("01_extraction.json", lambda data: data["key_facts"][0].update(quote="при условии что схему зонирования мы получим на этой неделе. Схему зонирования мы передадим до пятницы", timestamp="00:08:18"))
        code, report = self.validate()
        self.assertEqual(code, 1)
        spliced = [item for item in report["errors"] if item.startswith("склейка реплик разных говорящих")]
        self.assertEqual(len(spliced), 2, report["errors"])
        self.assertIn("Speaker 2 (00:08:18), Speaker 3 (00:08:32)", spliced[0])
        self.assertEqual(report["quote_stats"]["spliced"], 2)
        self.assertFalse(any("quote not found" in item for item in report["errors"]))
        manifest = payload(run(self.root, "manifest", "--meeting", MEETING))
        self.assertEqual(manifest["overall_status"], "awaiting_work")

    def test_quote_attributed_to_wrong_person_is_error(self) -> None:
        self.prepare_valid_meeting()
        # тактика t-03 (00:06:49) — реплика Speaker 2 = Олег Данилов, приписана Марине Зайцевой
        self.edit("02_tactics.json", lambda data: data["voss"][1].update(used_by="p-zaytseva-polaris"))
        # позиция карты аргументов: доказательство во вложенном списке evidence
        self.edit("10_argument_map.json", lambda data: data["argument_clusters"][0]["positions"][0].update(speaker="p-krylova-polaris"))
        code, report = self.validate()
        self.assertEqual(code, 1)
        mismatches = [item for item in report["errors"] if item.startswith("атрибуция цитаты не совпадает с картой спикеров")]
        self.assertEqual(len(mismatches), 2, report["errors"])
        self.assertIn("Олег Данилов", mismatches[0])
        self.assertEqual(report["quote_stats"]["attribution_mismatched"], 2)
        record = self.check_for(report, "02_tactics.json", "$.voss[1].source_quote")
        self.assertEqual(record["attribution"]["result"], "mismatch")

    def test_unconfirmed_map_turns_attribution_mismatch_into_warning(self) -> None:
        self.register()
        self.gate()
        self.copy_analysis()
        speakers ="Speaker 1=Анна Крылова;Speaker 2=Олег Данилов;Speaker 3=Марина Зайцева"
        review_path = self.meeting / "review.yaml"
        # предложенная, но не подтверждённая карта (как в режиме, когда пользователь недоступен)
        self.assertEqual(run(self.root, "speakers", "set", "--meeting", MEETING, "--map", speakers).returncode, 0)
        review_path.write_text(review_path.read_text(encoding="utf-8").replace('status: "confirmed"', 'status: "proposed"', 1), encoding="utf-8")
        self.edit("02_tactics.json", lambda data: data["voss"][1].update(used_by="p-zaytseva-polaris"))
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["source_quality"]["speaker_map_status"], "proposed")
        self.assertTrue(any("карта не подтверждена — предупреждение" in item for item in report["warnings"]))

    def test_commitment_anchor_by_other_person_is_warning(self) -> None:
        self.prepare_valid_meeting()
        # исполнитель обязательства ≠ говорящий якоря: по правилам опорой может быть принятие другой стороной
        self.edit("07_commitments.json", lambda data: data["commitments"][0].update(who="p-krylova-polaris"))
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertTrue(any(item.startswith("якорь обязательства произнёс не исполнитель") for item in report["warnings"]))

    def test_large_timestamp_drift_is_warning(self) -> None:
        self.prepare_valid_meeting()
        # цитата в 00:08:18; 00:09:45 не совпадает с началом ни одной реплики — дрейф, а не ссылка на другую реплику
        self.edit("07_commitments.json", lambda data: data["commitments"][0].update(source_timestamp="00:09:45"))
        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["quote_stats"]["timestamp_drift"], 1)
        drift = [item for item in report["warnings"] if item.startswith("таймкод расходится с репликой цитаты")]
        self.assertEqual(len(drift), 1)
        self.assertIn("74 с", drift[0])
        record = self.check_for(report, "07_commitments.json", "$.commitments[0].source_quote")
        self.assertEqual((record["timestamp_check"], record["timestamp_delta_sec"]), ("drift", 74))

    def test_timestamp_pointing_to_another_reply_is_error(self) -> None:
        self.prepare_valid_meeting()
        # цитата в 00:02:27, а заявлен таймкод начала другой реплики (00:08:18) — ссылка ведёт не туда
        self.edit("01_extraction.json", lambda data: data["key_facts"][0].update(timestamp="00:08:18"))
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertTrue(any(item.startswith("таймкод указывает на другую реплику: 01_extraction.json $.key_facts[0].quote") for item in report["errors"]), report["errors"])
        self.assertEqual(report["quote_stats"]["timestamp_other_reply"], 1)


class LightProfileTests(QuoteTestCase):
    def test_light_profile_reaches_ready_with_01_07_08(self) -> None:
        self.register()
        self.gate()
        self.assertEqual(run(self.root, "speakers", "set", "--meeting", MEETING, "--map", SPEAKER_MAP).returncode, 0)
        profile = run(self.root, "profile", "set", "--meeting", MEETING, "--profile", "light")
        self.assertEqual(profile.returncode, 0, profile.stdout + profile.stderr)
        self.assertEqual(payload(profile)["required"], list(LIGHT_FILES))
        for name in LIGHT_FILES:
            shutil.copyfile(FIXTURE / "analysis" / name, self.meeting / "analysis" / name)

        code, report = self.validate()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["analysis_profile"], "light")
        self.assertEqual(report["required_analysis_files"], 3)
        self.assertEqual(len(report["skipped_by_profile"]), 8)
        self.assertEqual(report["quote_stats"]["exact"], report["checked_quotes"])

        self.assertEqual(run(self.root, "report", "--meeting", MEETING).returncode, 0)
        text = (self.meeting / "report_auto.md").read_text(encoding="utf-8")
        self.assertIn("## Не выполнялось по профилю", text)
        self.assertIn("`09_decision_authority.json` — карта решений и полномочий", text)
        self.assertNotIn("## Наблюдённые тактики", text)
        self.assertIn("лёгкий разбор", text.splitlines()[2])
        self.assertIn("прислать уточнённую смету по трём складам", text)
        self.assertEqual(run(self.root, "followup", "--meeting", MEETING).returncode, 0)
        self.assertEqual(read_json(self.meeting / "follow_up.json")["analysis_profile"], "light")

        # база: обязательства, риски, люди и темы (решений лёгкий разбор не даёт)
        (self.root / "knowledge" / "people").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE / "existing_p-krylova-polaris.md", self.root / "knowledge" / "people" / "p-krylova-polaris.md")
        proposal = read_json(FIXTURE / "knowledge_proposal.json")
        proposal["records"] = [item for item in proposal["records"] if not item["registry"].startswith("knowledge/decisions/")]
        (self.meeting / "knowledge_proposal.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(run(self.root, "knowledge", "diff", "--meeting", MEETING).returncode, 0)
        self.assertEqual(run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm").returncode, 0)
        shutil.copyfile(self.meeting / "report_auto.md", self.meeting / "report.md")

        refreshed = run(self.root, "refresh-all")
        self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
        manifest = read_json(self.meeting / "run_manifest.json")
        self.assertEqual(manifest["overall_status"], "ready")
        self.assertEqual(manifest["analysis_profile"]["profile"], "light")
        status = payload(run(self.root, "status"))
        self.assertEqual([(row["overall_status"], row["analysis_profile"]) for row in status["meetings"]], [("ready", "light")])
        center = read_json(self.root / "system" / "control_center.json")
        self.assertEqual(center["meetings"][0]["pipeline_status"], "ready")

        # переход к полному разбору: недостающие артефакты снова обязательны
        self.assertEqual(run(self.root, "profile", "set", "--meeting", MEETING, "--profile", "full").returncode, 0)
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("missing analysis file: 02_tactics.json", report["errors"])
        manifest = payload(run(self.root, "status", "--meeting", MEETING))
        self.assertEqual((manifest["overall_status"], manifest["resume_from"]), ("awaiting_work", "analysis_complete"))

    def test_full_profile_without_02_is_not_ready(self) -> None:
        self.register()
        self.gate()
        self.assertEqual(run(self.root, "speakers", "set", "--meeting", MEETING, "--map", SPEAKER_MAP).returncode, 0)
        for name in ANALYSIS_FILES:
            if name != "02_tactics.json":
                shutil.copyfile(FIXTURE / "analysis" / name, self.meeting / "analysis" / name)
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertEqual(report["analysis_profile"], "full")
        self.assertIn("missing analysis file: 02_tactics.json", report["errors"])
        refreshed = payload(run(self.root, "refresh", "--meeting", MEETING))
        self.assertEqual((refreshed["overall_status"], refreshed["resume_from"]), ("awaiting_work", "analysis_complete"))

    def test_unknown_profile_value_blocks_validation(self) -> None:
        self.prepare_valid_meeting()
        review_path = self.meeting / "review.yaml"
        review_path.write_text(review_path.read_text(encoding="utf-8") + "analysis_profile: medium\n", encoding="utf-8")
        code, report = self.validate()
        self.assertEqual(code, 1)
        self.assertTrue(any("неизвестный профиль разбора" in item for item in report["errors"]))
