"""Default full-analysis methodology: real references, coverage and report readiness."""

from __future__ import annotations

import copy
from typing import Any, Callable

from run_tests import MEETING, SPEAKER_MAP, WorkspaceTestCase, read_json, run
from na_engine.analysis_profile import set_profile
from na_engine.common import read_yaml, write_json, write_yaml
from na_engine.galiev_review import HEADING, REVIEW_FILE, check_review, render_review, report_errors
from na_engine.pipeline import FOLLOW_UP_FILES, build_manifest, combined_digest, input_fingerprints, refresh_meeting


class GalievReviewTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.register()
        self.copy_analysis()

    def edit(self, mutate: Callable[[dict[str, Any]], None]) -> None:
        path = self.meeting / "analysis" / REVIEW_FILE
        data = read_json(path)
        mutate(data)
        write_json(path, data)

    def assert_invalid(self, fragment: str) -> None:
        result = check_review(self.meeting)
        self.assertTrue(result["required"])
        self.assertFalse(result["valid"], result)
        self.assertTrue(any(fragment in item for item in result["errors"]), result["errors"])

    def test_new_source_requires_review_and_fixture_has_verified_sources(self) -> None:
        policy = read_yaml(self.meeting / "review.yaml")["galiev_review"]
        self.assertEqual(policy, {"required": True, "source": "plugin_default", "policy_version": 1})
        result = check_review(self.meeting)
        self.assertTrue(result["required"])
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["checked_quotes"], 3)

    def test_missing_artifact_blocks_full_but_not_light_or_legacy(self) -> None:
        (self.meeting / "analysis" / REVIEW_FILE).unlink()
        self.assert_invalid(REVIEW_FILE)
        set_profile(self.root, MEETING, "light")
        self.assertEqual((check_review(self.meeting)["required"], check_review(self.meeting)["valid"]), (False, True))
        review = read_yaml(self.meeting / "review.yaml")
        review.pop("galiev_review")
        review.pop("analysis_profile")
        write_yaml(self.meeting / "review.yaml", review)
        self.assertFalse(check_review(self.meeting)["required"])
        set_profile(self.root, MEETING, "full")
        self.assert_invalid(REVIEW_FILE)

    def test_transcript_change_invalidates_review(self) -> None:
        with (self.meeting / "transcript.txt").open("a", encoding="utf-8") as handle:
            handle.write("\n[00:59:00] Speaker 1: Дополнение.\n")
        self.assert_invalid("transcript_sha256")

    def test_every_participant_exactly_once(self) -> None:
        original = read_json(self.meeting / "analysis" / REVIEW_FILE)
        self.edit(lambda data: data["participants"].pop())
        self.assert_invalid("не охвачены")
        write_json(self.meeting / "analysis" / REVIEW_FILE, original)
        self.edit(lambda data: data["participants"].append(copy.deepcopy(data["participants"][0])))
        self.assert_invalid("повторяется")

    def test_canonical_reference_is_bound_to_heading_and_file(self) -> None:
        original = read_json(self.meeting / "analysis" / REVIEW_FILE)
        self.edit(lambda data: data["principles"][0].update(file="../casos.json"))
        self.assert_invalid("файлом канона")
        write_json(self.meeting / "analysis" / REVIEW_FILE, original)
        self.edit(lambda data: data["principles"][0].update(heading="Несуществующий принцип"))
        self.assert_invalid("heading")
        write_json(self.meeting / "analysis" / REVIEW_FILE, original)
        self.edit(lambda data: data["principles"][0].update(source_quote="Ролевой щит"))
        self.assert_invalid("внутри указанного принципа")

    def test_case_identity_and_reference_must_match_real_library(self) -> None:
        original = read_json(self.meeting / "analysis" / REVIEW_FILE)
        self.edit(lambda data: data["cases"][0].update(title="Выдуманный случай"))
        self.assert_invalid("реальным случаем")
        write_json(self.meeting / "analysis" / REVIEW_FILE, original)
        self.edit(lambda data: data["participants"][0].update(case_refs=[999999]))
        self.assert_invalid("case_refs")

    def test_search_can_find_no_suitable_case_with_reason(self) -> None:
        def no_case(data: dict[str, Any]) -> None:
            data["cases"] = []
            data["case_search"]["no_match_reason"] = "Найденные сюжеты относятся к иным отношениям; сопоставимого случая нет."
            for participant in data["participants"]:
                participant["case_refs"] = []
        self.edit(no_case)
        self.assertTrue(check_review(self.meeting)["valid"])
        self.edit(lambda data: data["case_search"].update(no_match_reason=None))
        self.assert_invalid("no_match_reason")

    def test_insufficient_evidence_is_explicit_and_contains_no_assessment(self) -> None:
        def insufficient(data: dict[str, Any]) -> None:
            data["participants"][0].update(status="insufficient_evidence", assessment=None, alternative=None,
                evidence=[], principle_refs=[], case_refs=[], next_steps=[], limitations=["Данных для оценки поведения недостаточно."])
        self.edit(insufficient)
        result = check_review(self.meeting)
        self.assertTrue(result["valid"], result["errors"])
        self.assertIn("Недостаточно данных", render_review(result["data"]))
        self.edit(lambda data: data["participants"][0].update(assessment="Неподтверждённая оценка"))
        self.assert_invalid("нельзя добавлять оценку")

    def test_assessment_needs_alternative_evidence_principle_and_tradeoff(self) -> None:
        original = read_json(self.meeting / "analysis" / REVIEW_FILE)
        for field, value in (("alternative", ""), ("evidence", []), ("principle_refs", []), ("next_steps", [{"action": "Уточнить", "tradeoff": ""}])):
            with self.subTest(field=field):
                data = copy.deepcopy(original)
                data["participants"][0][field] = value
                write_json(self.meeting / "analysis" / REVIEW_FILE, data)
                self.assert_invalid(field)

    def test_no_applicable_principle_allows_honest_insufficient_evidence(self) -> None:
        def no_principle(data: dict[str, Any]) -> None:
            data["principles"] = []
            data["limitations"] = ["По доступному фрагменту применимый принцип не установлен; оценка поведения не производится."]
            for participant in data["participants"]:
                participant.update(status="insufficient_evidence", assessment=None, alternative=None,
                    evidence=[], principle_refs=[], case_refs=[], next_steps=[], limitations=["Недостаточно данных для обоснованного применения принципа к участнику."])
        self.edit(no_principle)
        result = check_review(self.meeting)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(report_errors(self.meeting, render_review(result["data"])), [])
        self.edit(lambda data: data.update(limitations=[]))
        self.assert_invalid("без применимого принципа")

    def test_evidence_quote_and_timestamp_use_core_quote_checker(self) -> None:
        original = read_json(self.meeting / "analysis" / REVIEW_FILE)
        self.edit(lambda data: data["participants"][0]["evidence"][0].update(quote="Цитата, которой в записи нет"))
        self.assertFalse(check_review(self.meeting)["valid"])
        write_json(self.meeting / "analysis" / REVIEW_FILE, original)
        self.edit(lambda data: data["participants"][0]["evidence"][0].update(timestamp="00:00:04"))
        self.assertFalse(check_review(self.meeting)["valid"])

    def test_evidence_must_belong_to_assessed_participant(self) -> None:
        mapped = run(self.root, "speakers", "set", "--meeting", MEETING, "--map", SPEAKER_MAP)
        self.assertEqual(mapped.returncode, 0, mapped.stdout + mapped.stderr)
        self.assertTrue(check_review(self.meeting)["valid"])
        self.edit(lambda data: data["participants"][0].update(evidence=data["participants"][1]["evidence"]))
        self.assertFalse(check_review(self.meeting)["valid"])

    def test_report_requires_substance_not_only_heading(self) -> None:
        result = check_review(self.meeting)
        data = result["data"]
        self.assertTrue(report_errors(self.meeting, HEADING))
        text = render_review(data, {"p-krylova-polaris": "Анна Крылова"})
        self.assertIn("### Анна Крылова", text)
        self.assertIn("Альтернативное объяснение", text)
        self.assertIn("Цена/риск", text)
        self.assertEqual(report_errors(self.meeting, text), [])
        self.assertTrue(report_errors(self.meeting, text.replace(data["participants"][0]["assessment"], "")))
        self.assertTrue(report_errors(self.meeting, text.replace(data["cases"][0]["difference"], "")))
        self.assertTrue(report_errors(self.meeting, text.replace(data["limitations"][0], "")))
        # A heading with all method content elsewhere is still an empty chapter.
        self.assertTrue(report_errors(self.meeting, text.replace(HEADING, "## Другая глава") + "\n" + HEADING))

    def test_pipeline_blocks_missing_review_and_final_omission_and_tracks_artifact(self) -> None:
        data = read_json(self.meeting / "analysis" / REVIEW_FILE)
        (self.meeting / "report.md").write_text(HEADING, encoding="utf-8")
        manifest = build_manifest(self.root, self.meeting)
        stages = {item["id"]: item for item in manifest["stages"]}
        self.assertEqual(stages["galiev_review_validated"]["status"], "completed")
        self.assertEqual(stages["human_report_ready"]["status"], "pending")
        (self.meeting / "report.md").write_text(render_review(data), encoding="utf-8")
        manifest = build_manifest(self.root, self.meeting)
        self.assertEqual(next(s for s in manifest["stages"] if s["id"] == "human_report_ready")["status"], "completed")
        digest = combined_digest(input_fingerprints(self.root, self.meeting))
        self.edit(lambda item: item.update(summary=item["summary"] + " Изменено."))
        self.assertNotEqual(digest, combined_digest(input_fingerprints(self.root, self.meeting)))
        self.assertTrue(any(item["path"].endswith(REVIEW_FILE) for item in manifest["artifact_fingerprints"]))
        (self.meeting / "analysis" / REVIEW_FILE).unlink()
        stage = next(s for s in build_manifest(self.root, self.meeting)["stages"] if s["id"] == "galiev_review_validated")
        self.assertEqual((stage["status"], stage["blocking"]), ("pending", True))

    def test_core_validation_stays_eleven_contracts_and_report_enforces_method(self) -> None:
        self.assertEqual(run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "yes", "--masking", "none", "--sensitivity", "internal", "--routes", "local_only", "--retain-source", "yes").returncode, 0)
        (self.meeting / "analysis" / REVIEW_FILE).unlink()
        core = run(self.root, "validate", "--meeting", MEETING)
        self.assertEqual(core.returncode, 0, core.stdout + core.stderr)
        report = run(self.root, "report", "--meeting", MEETING)
        self.assertNotEqual(report.returncode, 0)
        self.assertIn(REVIEW_FILE, report.stdout + report.stderr)

    def test_cached_ready_does_not_hide_removed_method_chapter(self) -> None:
        complete = render_review(read_json(self.meeting / "analysis" / REVIEW_FILE))
        for output in ("report_auto.md", "report.md"):
            with self.subTest(output=output):
                for name in ("report_auto.md", "report.md", "analysis/validation_report.json", "analysis/report_auto_validation.json", *FOLLOW_UP_FILES):
                    path = self.meeting / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(complete if name.endswith(".md") else "{}", encoding="utf-8")
                (self.meeting / output).write_text(HEADING, encoding="utf-8")
                write_json(self.meeting / "run_manifest.json", {"overall_status": "ready", "pipeline_input_digest": combined_digest(input_fingerprints(self.root, self.meeting))})
                refreshed = refresh_meeting(self.root, self.meeting)
                self.assertEqual(refreshed["status"], "refreshed")
                self.assertNotEqual(refreshed["overall_status"], "ready")
