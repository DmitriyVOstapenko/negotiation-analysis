"""Длинная встреча по частям (na.py parts plan|check|merge) и отчёт верификации (na.py verification check)."""

from __future__ import annotations

import copy
import json
import shutil
import unittest
from pathlib import Path

from run_tests import FIXTURE, MEETING, WorkspaceTestCase, payload, read_json, run
from na_engine.long_meeting import merge_lines  # run_tests добавил scripts/ в sys.path

LIST_FIELDS_FROM_ALL_PARTS = {("01_extraction", "participants"), ("01_extraction", "companies")}
CONFIDENCE = {"certain": 3, "probable": 2, "possible": 1, "speculative": 0}


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def canon(items: list) -> list[str]:
    return sorted(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items)


class PartsTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.prepare_valid_meeting()
        self.parts_dir = self.meeting / "analysis" / "parts"

    def plan(self, max_chars: int = 4000) -> dict:
        completed = run(self.root, "parts", "plan", "--meeting", MEETING, "--max-chars", str(max_chars))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return payload(completed)

    def part_of(self, plan: dict, quote: str) -> int | None:
        lines = (self.meeting / "transcript.txt").read_text(encoding="utf-8").splitlines()
        for part in plan["parts"]:
            if quote in "\n".join(lines[part["line_from"] - 1 : part["line_to"]]):
                return part["part"]
        return None

    def item_part(self, plan: dict, item: object) -> int:
        if isinstance(item, dict):
            for key in ("source_quote", "quote", "evidence_quote"):
                if isinstance(item.get(key), str):
                    return self.part_of(plan, item[key]) or 1
            for key in ("source_quotes", "evidence"):
                if isinstance(item.get(key), list) and item[key]:
                    return self.part_of(plan, item[key][0]["quote"]) or 1
        return 1

    def split_fixture(self, plan: dict, stems: tuple[str, ...]) -> None:
        """Разложить артефакты синтетической встречи по частям — как их вернули бы аналитики частей."""
        for stem in stems:
            data = read_json(FIXTURE / "analysis" / f"{stem}.json")
            for part in plan["parts"]:
                out = {}
                for key, value in data.items():
                    if isinstance(value, list) and (stem, key) not in LIST_FIELDS_FROM_ALL_PARTS:
                        out[key] = [item for item in value if self.item_part(plan, item) == part["part"]]
                    else:
                        out[key] = copy.deepcopy(value)
                write(self.parts_dir / f"{stem}.part-{part['part']}.json", out)

    def test_plan_cuts_by_replicas_and_prints_no_transcript(self) -> None:
        plain = run(self.root, "--json", "parts", "plan", "--meeting", MEETING)
        self.assertEqual(plain.returncode, 0)
        result = self.plan(3000)
        parts = result["parts"]
        self.assertGreater(len(parts), 2)
        self.assertEqual(parts[0]["line_from"], 1)
        self.assertEqual(parts[-1]["line_to"], result["total_lines"])
        for previous, current in zip(parts, parts[1:]):
            self.assertEqual(current["line_from"], previous["line_to"] + 1)
            self.assertLessEqual(current["context_from_line"], current["line_from"])
        self.assertTrue(all(part["chars"] <= 3000 or part["oversized_replica"] for part in parts))
        self.assertFalse(result["long"])
        self.assertEqual(result["replicas"], sum(part["replicas"] for part in parts))
        self.assertTrue((self.parts_dir / "plan.json").is_file())
        text_run = run(self.root, "parts", "plan", "--meeting", MEETING, "--max-chars", "3000")
        transcript_line = (self.meeting / "transcript.txt").read_text(encoding="utf-8").splitlines()[0]
        self.assertNotIn(transcript_line[12:60], text_run.stdout)

    def test_long_meeting_detected(self) -> None:
        lines = []
        for minute in range(0, 75):
            lines.append(f"[{minute // 60:02d}:{minute % 60:02d}:05] Speaker {minute % 2 + 1}: Обсуждаем пункт {minute}, нужна смета и сроки.")
        source = self.tmp / "long.txt"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(run(self.root, "source", "create", "--meeting", "2026-11-01-long", "--transcript", str(source)).returncode, 0)
        result = payload(run(self.root, "parts", "plan", "--meeting", "2026-11-01-long"))
        self.assertTrue(result["long"])
        self.assertTrue(any("длительность" in reason for reason in result["long_reasons"]))

    def test_split_check_merge_validate(self) -> None:
        plan = self.plan(4000)
        self.assertEqual(len(plan["parts"]), 2)
        for name in ("01_extraction", "02_tactics", "03_manipulations", "04_arguments", "06_emotions", "07_commitments"):
            (self.meeting / "analysis" / f"{name}.json").unlink()
        self.split_fixture(plan, ("01_extraction", "02_tactics", "03_manipulations", "04_arguments", "06_emotions", "07_commitments"))
        check = run(self.root, "parts", "check", "--meeting", MEETING)
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertTrue(payload(check)["valid"])
        merge = run(self.root, "parts", "merge", "--meeting", MEETING)
        self.assertEqual(merge.returncode, 0, merge.stdout)
        report = payload(merge)
        fields = {entry["field"] for item in report["merged"] for entry in item["review_fields"]}
        self.assertTrue({"outcome", "outcome_summary", "conclusion", "overall_summary.general_trend"} <= fields)
        for stem in ("01_extraction", "02_tactics", "04_arguments", "07_commitments"):
            original, merged = read_json(FIXTURE / "analysis" / f"{stem}.json"), read_json(self.meeting / "analysis" / f"{stem}.json")
            for key, value in original.items():
                if isinstance(value, list):
                    self.assertEqual(canon(merged[key]), canon(value), f"{stem}.{key}")
        validate = run(self.root, "validate", "--meeting", MEETING)
        self.assertEqual(validate.returncode, 0, validate.stdout)
        again = run(self.root, "parts", "merge", "--meeting", MEETING, "--artifact", "07")
        self.assertEqual(again.returncode, 1)
        self.assertEqual(payload(again)["skipped"][0]["artifact"], "07_commitments.json")
        self.assertEqual(run(self.root, "parts", "merge", "--meeting", MEETING, "--artifact", "07", "--force").returncode, 0)

    def test_check_rejects_foreign_and_invented_quotes(self) -> None:
        plan = self.plan(4000)
        self.split_fixture(plan, ("07_commitments",))
        part1 = self.parts_dir / "07_commitments.part-1.json"
        part2 = read_json(self.parts_dir / "07_commitments.part-2.json")
        data = read_json(part1)
        data["commitments"].append(copy.deepcopy(part2["commitments"][0]))
        write(part1, data)
        result = run(self.root, "parts", "check", "--meeting", MEETING, "--file", part1.name)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any("вне своей части" in error for item in payload(result)["files"] for error in item["errors"]))
        data["commitments"] = [copy.deepcopy(part2["commitments"][0])]
        data["commitments"][0]["source_quote"] = "Такой фразы на встрече не было."
        write(part1, data)
        result = run(self.root, "parts", "check", "--meeting", MEETING, "--file", part1.name)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any("not found" in error for item in payload(result)["files"] for error in item["errors"]))

    def test_merge_power_weights_sums_and_keyed_entities(self) -> None:
        plan = self.plan(4000)
        weights = {part["part"]: part["chars"] for part in plan["parts"]}
        base = read_json(FIXTURE / "analysis" / "05_power.json")
        first, second = copy.deepcopy(base), copy.deepcopy(base)
        for item in (first, second):
            item["framing_moments"], item["concessions"] = [], []
        first["talk_time_share"] = [{"person_id": "p-a", "share_pct": 80}, {"person_id": "p-b", "share_pct": 20}]
        second["talk_time_share"] = [{"person_id": "p-a", "share_pct": 20}, {"person_id": "p-b", "share_pct": 80}]
        first["question_marks_in_transcript"] = [{"person_id": "p-a", "count": 2}]
        second["question_marks_in_transcript"] = [{"person_id": "p-a", "count": 3}, {"person_id": "p-b", "count": 1}]
        first["interruptions"] = [{"who": "p-a", "whom": "p-b", "count": 1}]
        second["interruptions"] = [{"who": "p-a", "whom": "p-b", "count": 2}]
        write(self.parts_dir / "05_power.part-1.json", first)
        write(self.parts_dir / "05_power.part-2.json", second)
        extraction = read_json(FIXTURE / "analysis" / "01_extraction.json")
        weak, strong = copy.deepcopy(extraction), copy.deepcopy(extraction)
        weak["participants"][0]["confidence"] = "possible"
        strong["participants"][0]["confidence"] = "certain"
        strong["participants"][0]["role_freeform"] = "уточнено во второй части"
        write(self.parts_dir / "01_extraction.part-1.json", weak)
        write(self.parts_dir / "01_extraction.part-2.json", strong)
        result = payload(run(self.root, "parts", "merge", "--meeting", MEETING, "--artifact", "01,05", "--force"))
        power = read_json(self.meeting / "analysis" / "05_power.json")
        total = weights[1] + weights[2]
        expected_a = round((80 * weights[1] + 20 * weights[2]) / total, 1)
        shares = {item["person_id"]: item["share_pct"] for item in power["talk_time_share"]}
        self.assertAlmostEqual(shares["p-a"], expected_a, places=1)
        self.assertEqual({item["person_id"]: item["count"] for item in power["question_marks_in_transcript"]}, {"p-a": 5, "p-b": 1})
        self.assertEqual(power["interruptions"], [{"who": "p-a", "whom": "p-b", "count": 3}])
        self.assertEqual(power["dominant_speaker"], "p-a" if shares["p-a"] >= shares["p-b"] else "p-b")
        merged = read_json(self.meeting / "analysis" / "01_extraction.json")
        self.assertEqual(len(merged["participants"]), len(extraction["participants"]))
        self.assertEqual(merged["participants"][0]["confidence"], "certain")
        conflicts = [entry for item in result["merged"] for entry in item["conflicts"]]
        self.assertTrue(any(entry["field"] == "participants" and entry["kept_from_part"] == 2 for entry in conflicts))

    def commitments_in_three_parts(self) -> tuple[dict, dict, dict]:
        """Встреча из трёх частей, как 08: обещания в частях 1 и 2, повтор в итогах — в части 3."""
        plan = self.plan(3000)
        self.assertEqual(len(plan["parts"]), 3)
        self.split_fixture(plan, ("07_commitments",))
        base = read_json(FIXTURE / "analysis" / "07_commitments.json")
        docs = next(item for item in base["commitments"] if item["source_timestamp"] == "00:04:01")
        estimate = next(item for item in base["commitments"] if item["source_timestamp"] == "00:08:18")
        self.assertEqual((self.part_of(plan, docs["source_quote"]), self.part_of(plan, estimate["source_quote"])), (1, 2))
        return base, docs, estimate

    def merge_07(self) -> tuple[dict, list]:
        merge = run(self.root, "parts", "merge", "--meeting", MEETING, "--artifact", "07", "--force")
        self.assertEqual(merge.returncode, 0, merge.stdout)
        return payload(merge)["merged"][0], read_json(self.meeting / "analysis" / "07_commitments.json")["commitments"]

    def test_merge_joins_commitment_repeated_in_later_part(self) -> None:
        base, docs, estimate = self.commitments_in_three_parts()
        question = {"quote": "Кто в таком случае несёт расходы на восстановление документации?", "timestamp": "00:03:36", "note": "вопрос другой стороны"}
        docs_summary = {"quote": "запрос документации у прежнего подрядчика — беру на себя", "timestamp": "00:11:13", "note": "подтверждено в итогах"}
        estimate_summary = {"quote": "Первое: мы присылаем уточнённую смету по трём складам до пятого ноября.", "timestamp": "00:10:42", "note": "подтверждено в итогах"}
        first = read_json(self.parts_dir / "07_commitments.part-1.json")
        next(item for item in first["commitments"] if item["id"] == docs["id"])["related_mentions"] = [question]
        write(self.parts_dir / "07_commitments.part-1.json", first)
        # аналитик части 3 записал те же обязательства с повтором в итогах; уже известная реплика — без note
        third = read_json(self.parts_dir / "07_commitments.part-3.json")
        third["commitments"] += [
            {**docs, "related_mentions": [{**question, "note": None}, docs_summary]},
            {**estimate, "related_mentions": [estimate_summary]},
        ]
        write(self.parts_dir / "07_commitments.part-3.json", third)

        report, merged = self.merge_07()
        self.assertEqual(sorted(item["id"] for item in merged), sorted(item["id"] for item in base["commitments"]))
        by_id = {item["id"]: item for item in merged}
        self.assertEqual(by_id[docs["id"]]["related_mentions"], [question, docs_summary])
        self.assertEqual(by_id[estimate["id"]]["related_mentions"], [estimate_summary])
        self.assertEqual({key: value for key, value in by_id[docs["id"]].items() if key != "related_mentions"}, docs)
        self.assertEqual(list(by_id[estimate["id"]])[-2:], ["related_mentions", "follow_up"])
        self.assertEqual((report["renamed_ids"], report["conflicts"], report["possible_duplicates"]), ([], [], []))
        joined = {entry["id"]: entry for entry in report["joined"]}
        self.assertEqual((joined[docs["id"]]["parts"], joined[docs["id"]]["related_mentions_added"]), ([1, 3], 1))
        self.assertEqual((joined[estimate["id"]]["parts"], joined[estimate["id"]]["related_mentions_added"]), ([2, 3], 1))
        text = "\n".join(merge_lines({"meeting_id": MEETING, "merged": [report], "skipped": [], "warnings": []}))
        self.assertIn(f"{docs['id']}: части 1, 3 сведены в одну запись, связанных реплик добавлено 1", text)
        validate = run(self.root, "validate", "--meeting", MEETING)
        self.assertEqual(validate.returncode, 0, validate.stdout)

    def test_merge_commitment_conflicts_and_possible_duplicates(self) -> None:
        base, docs, estimate = self.commitments_in_three_parts()
        third = read_json(self.parts_dir / "07_commitments.part-3.json")
        # та же запись с другой уверенностью: одна запись, расхождение в conflicts
        stronger = {**estimate, "id": "c-estimate-part-3", "confidence": "certain" if estimate["confidence"] != "certain" else "probable"}
        # опора — повтор в итогах, формулировка своя: не сводится, но помечается как возможный дубль
        first = read_json(self.parts_dir / "07_commitments.part-1.json")
        next(item for item in first["commitments"] if item["id"] == docs["id"])["related_mentions"] = [{"quote": "запрос документации у прежнего подрядчика — беру на себя", "timestamp": "00:11:13"}]
        write(self.parts_dir / "07_commitments.part-1.json", first)
        summary_anchor = {**docs, "id": "c-docs-summary", "what": "запросить документацию у прежнего подрядчика", "source_quote": "запрос документации у прежнего подрядчика — беру на себя", "source_timestamp": "00:11:13"}
        third["commitments"] += [stronger, summary_anchor]
        write(self.parts_dir / "07_commitments.part-3.json", third)

        report, merged = self.merge_07()
        self.assertEqual(len(merged), len(base["commitments"]) + 1)
        kept = [item for item in merged if item["source_timestamp"] == estimate["source_timestamp"]]
        self.assertEqual(len(kept), 1)
        better = CONFIDENCE[stronger["confidence"]] > CONFIDENCE[estimate["confidence"]]
        self.assertEqual(kept[0]["id"], stronger["id"] if better else estimate["id"])
        conflict = report["conflicts"][0]
        self.assertEqual((conflict["parts"], conflict["differs"], conflict["kept_from_part"]), ([2, 3], ["confidence", "id"], 3 if better else 2))
        self.assertEqual(report["joined"][0]["absorbed_ids"], [estimate["id"] if better else stronger["id"]])
        self.assertEqual(report["possible_duplicates"], [{"field": "commitments", "ids": [docs["id"], "c-docs-summary"], "parts": [1, 3], "reason": "опора одной записи — связанная реплика другой у того же исполнителя"}])

    def test_merge_renames_colliding_ids(self) -> None:
        self.plan(4000)
        base = read_json(FIXTURE / "analysis" / "04_arguments.json")
        one, two = copy.deepcopy(base), copy.deepcopy(base)
        one["arguments"] = [base["arguments"][0]]
        two["arguments"] = [{**base["arguments"][1], "id": base["arguments"][0]["id"]}]
        write(self.parts_dir / "04_arguments.part-1.json", one)
        write(self.parts_dir / "04_arguments.part-2.json", two)
        result = payload(run(self.root, "parts", "merge", "--meeting", MEETING, "--artifact", "04", "--force"))
        ids = [item["id"] for item in read_json(self.meeting / "analysis" / "04_arguments.json")["arguments"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(result["merged"][0]["renamed_ids"][0]["to"], f"{base['arguments'][0]['id']}-p2")


class VerificationTests(WorkspaceTestCase):
    def report(self) -> dict:
        commitment = read_json(FIXTURE / "analysis" / "07_commitments.json")["commitments"][0]
        return {
            "meeting_id": MEETING,
            "verified_at": None,
            "scope": ["07_commitments.json", "08_risks.json", "09_decision_authority.json", "01_extraction.json#participants"],
            "transcript_has_timestamps": True,
            "speaker_labels_present": True,
            "summary": {"checked": 5, "confirmed": 4, "contradicted": 0, "changed_later": 0, "unsupported": 1, "missed": 0},
            "findings": [
                {
                    "id": "v1",
                    "verdict": "unsupported",
                    "artifact": "07_commitments.json",
                    "path": "$.commitments[0].status",
                    "claim": "обязательство без условия",
                    "quote": commitment["source_quote"],
                    "timestamp": commitment["source_timestamp"],
                    "explanation": "обещание условное",
                    "question_to_user": "Отметить как условное? (да / нет)",
                }
            ],
            "limitations": "только текст",
        }

    def test_valid_and_invalid_reports(self) -> None:
        self.prepare_valid_meeting()
        target = self.meeting / "analysis" / "verification_report.json"
        missing = run(self.root, "verification", "check", "--meeting", MEETING)
        self.assertEqual(missing.returncode, 2)
        write(target, self.report())
        good = run(self.root, "verification", "check", "--meeting", MEETING)
        self.assertEqual(good.returncode, 0, good.stdout)
        self.assertEqual(payload(good)["counts"]["unsupported"], 1)

        bad = self.report()
        bad["findings"][0]["quote"] = "Этой фразы в транскрипте нет."
        bad["findings"].append({"id": "v2", "verdict": "lost", "artifact": "07_commitments.json", "path": "$.commitments[42]", "quote": "", "timestamp": None})
        write(target, bad)
        result = run(self.root, "verification", "check", "--meeting", MEETING)
        self.assertEqual(result.returncode, 1)
        errors = "\n".join(payload(result)["errors"])
        for needle in ("not found", "lost", "$.commitments[42]", "без цитаты", "таймкод"):
            self.assertIn(needle, errors)

        missed = self.report()
        missed["findings"][0].update({"verdict": "missed", "path": "$.commitments[0]"})
        missed["summary"].update({"unsupported": 0, "missed": 1})
        write(target, missed)
        result = run(self.root, "verification", "check", "--meeting", MEETING)
        self.assertEqual(result.returncode, 1)
        self.assertIn("адрес списка", "\n".join(payload(result)["errors"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
