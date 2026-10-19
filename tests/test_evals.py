"""Оценка разбора по несущему ядру: na.py eval, фикстура, реестр проверочных встреч tests/evals/.

Эталонный разбор для сценариев строится здесь же из core.json (минимальные 01/05/07/08/09):
он должен давать PASS — так проверяется, что ядро выполнимо и не противоречит само себе, а
ловушки из meta.json (типовые ошибки разбора) должны давать FAIL с ожидаемыми статусами.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path

from run_tests import FIXTURE, MEETING, REPO, SPEAKER_MAP, WorkspaceTestCase, payload, read_json, run

sys.path.insert(0, str(REPO / "scripts"))
from na_engine import evals  # noqa: E402

EVALS_DIR = REPO / "tests" / "evals"
FIXTURE_CORE = FIXTURE / "core.json"
SCENARIOS = sorted(path for path in EVALS_DIR.iterdir() if path.is_dir()) if EVALS_DIR.is_dir() else []
REQUIRED_CHECKS = {
    "duplicate_source", "partial_speaker_map", "source_instruction", "ai_proposal_not_accepted", "no_diarization",
    "asr_variants", "revised_volume", "revised_deadline", "unanswered_proposal", "cut_off_agreement", "yes_to_earlier",
    "cancelled_later",
}
PERSON_REF = "@person:"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Эталонный разбор из ядра и ловушки
# ---------------------------------------------------------------------------


def _pid(person: str) -> str:
    return f"x-{person}"


def _value(value):
    if isinstance(value, str) and value.startswith(PERSON_REF):
        return _pid(value[len(PERSON_REF):])
    return value


def _text(item: dict) -> str:
    firsts = [group[0] for key in ("keywords", "expect_text") for group in item.get(key) or []]
    return " ".join([item["title"], *firsts])


def _commitment(meeting_id: str, ident: str, who: str | None, what: str, quote: str, timestamp: str, deadline=None, verbatim=None, status: str = "open", confidence: str = "certain", to_whom=None) -> dict:
    return {
        "id": ident, "meeting_id": meeting_id, "type": "other", "who": who, "to_whom": to_whom, "what": what,
        "deadline": deadline, "deadline_verbatim": verbatim, "status": status, "value_rub": None,
        "source_quote": quote, "source_timestamp": timestamp, "confidence": confidence, "linked_topic": "topic",
        "follow_up": {"reminder_date": None, "reminder_sent": False, "actual_completion_date": None, "completion_notes": None},
    }


def _decision_area(decision: str, current_state: str, fdm, evidence: list, deadline=None) -> dict:
    return {
        "topic": "topic", "decision": decision, "options": [], "current_state": current_state, "final_decision_maker": fdm,
        "advisory_participants": [], "blocking_rights": None, "proposal_owner": None, "proposal_owner_source": None,
        "deadline": deadline, "deadline_source": "transcript" if deadline else None, "evidence": evidence, "confidence": "certain",
    }


def build_reference(core: dict, meeting_id: str) -> tuple[dict, dict]:
    """Минимальный разбор, в котором каждый пункт ядра отражён так, как требует ядро."""
    participants, by_person = [], {}
    for person in core["people"]:
        if not person.get("present", True):
            continue
        by_person[person["id"]] = len(participants)
        participants.append({
            "person_id": _pid(person["id"]), "full_name": (person.get("names") or person.get("labels"))[0], "role": "other",
            "role_freeform": None, "company_id": "org-x", "is_our_side": False, "identity_source": "user_confirmation", "confidence": "certain",
        })
    extraction = {"meeting_id": meeting_id, "participants": participants, "companies": [], "topics": [], "key_facts": [], "outcome": "partial", "outcome_summary": "эталон", "excluded_source_range": None}
    power = {"meeting_id": meeting_id, "concessions": []}
    commitments = {"meeting_id": meeting_id, "commitments": [], "open_questions": []}
    risks = {"meeting_id": meeting_id, "risks": []}
    authority = {"meeting_id": meeting_id, "view_type": "decision_authority_map", "status": "partially_defined", "decision_areas": [], "authority_observations": [], "unresolved": []}
    where: dict[str, dict] = {}
    for item in core["items"]:
        kind, anchors, text = item["kind"], item.get("anchors") or [], _text(item)
        anchor = anchors[0] if anchors else None
        evidence = [{"quote": anchor["quote"], "timestamp": anchor["timestamp"]}] if anchor else []
        if kind == "commitment":
            who = item["who"] if isinstance(item["who"], str) else item["who"][0]
            record = _commitment(meeting_id, f"c-{item['id']}", _pid(who), text, anchor["quote"], anchor["timestamp"], item.get("deadline"), (item.get("deadline_verbatim") or [None])[0], confidence=item.get("confidence_max", "certain"), to_whom=_pid(item["to_whom"]) if item.get("to_whom") else None)
            commitments["commitments"].append(record)
        elif kind == "risk":
            record = {
                "id": f"r-{item['id']}", "type": "other", "description": text, "first_seen_meeting_id": meeting_id, "last_seen_meeting_id": meeting_id,
                "involved_companies": [], "involved_people": [], "linked_topic": "topic", "likelihood": "medium", "impact_rub": None,
                "impact_qualitative": "high", "severity": "high", "mitigation": {"planned_actions": "—", "owner": _pid(item["owner"]) if item.get("owner") else None, "deadline": None},
                "status": "open", "evidence": [{"quote": anchor["quote"], "meeting_id": meeting_id, "timestamp": anchor["timestamp"]}], "confidence": "certain",
            }
            risks["risks"].append(record)
        elif kind == "concession":
            prior = (item.get("prior_anchor") or [anchor])[0]
            who = item["who"] if isinstance(item["who"], str) else item["who"][0]
            record = {"speaker": _pid(who), "quote": anchor["quote"], "timestamp": anchor["timestamp"], "prior_position_quote": prior["quote"], "prior_position_timestamp": prior["timestamp"]}
            power["concessions"].append(record)
        elif kind == "agreed_term":
            record = {"fact": text, "quote": anchor["quote"], "timestamp": anchor["timestamp"], "confidence": "certain"}
            extraction["key_facts"].append(record)
        elif kind == "authority":
            person = item.get("person")
            record = _decision_area(text, "в работе", _pid(person) if person else None, evidence)
            authority["decision_areas"].append(record)
            if person is None:
                authority["unresolved"].append(text)
        elif kind in ("not_decision", "open_question"):
            record = None
            if kind == "open_question" or item.get("must_be_reported", True):
                if anchor:
                    record = {"topic": "topic", "question": text, "quote": anchor["quote"], "timestamp": anchor["timestamp"]}
                    commitments["open_questions"].append(record)
                else:
                    authority["unresolved"].append(text)
            if item.get("deadline"):
                authority["decision_areas"].append(_decision_area(text, "отложено", None, evidence, item["deadline"]))
            if record is None:
                continue
        elif kind == "participant":
            record = participants[by_person[item["person"]]]
            if not item["confirmed"]:
                record["identity_source"], record["confidence"] = "transcript", "probable"
        elif kind == "outcome":
            extraction["outcome"] = item["outcome_any"][0]
            continue
        where[item["id"]] = record
    artifacts = {
        "01_extraction.json": extraction, "05_power.json": power, "07_commitments.json": commitments,
        "08_risks.json": risks, "09_decision_authority.json": authority,
    }
    return artifacts, where


def apply_mutations(artifacts: dict, where: dict, mutations: list, meeting_id: str) -> None:
    extraction, commitments, authority = artifacts["01_extraction.json"], artifacts["07_commitments.json"], artifacts["09_decision_authority.json"]
    for number, mutation in enumerate(mutations):
        op = mutation["op"]
        if op == "add_commitment":
            commitments["commitments"].append(_commitment(
                meeting_id, f"c-trap-{number}", _pid(mutation["who"]) if mutation.get("who") else None, mutation["what"], mutation["quote"], mutation["timestamp"],
                mutation.get("deadline"), mutation.get("deadline_verbatim"), mutation.get("status", "open"),
            ))
        elif op == "patch":
            target = where[mutation["item"]]
            for key, value in mutation["set"].items():
                target[key] = _value(value)
        elif op == "remove":
            target = where.pop(mutation["item"])
            for artifact in artifacts.values():
                for key, value in artifact.items():
                    if isinstance(value, list) and any(entry is target for entry in value):
                        artifact[key] = [entry for entry in value if entry is not target]
        elif op == "set_outcome":
            extraction["outcome"] = mutation["value"]
        elif op == "add_participant":
            extraction["participants"].append({
                "person_id": f"x-trap-{number}", "full_name": mutation["full_name"], "role": "other", "role_freeform": None, "company_id": "org-x",
                "is_our_side": False, "identity_source": mutation.get("identity_source", "transcript"), "confidence": mutation.get("confidence", "possible"),
            })
        elif op == "add_key_fact":
            extraction["key_facts"].append({"fact": mutation["fact"], "quote": mutation["quote"], "timestamp": mutation["timestamp"], "confidence": "certain"})
        elif op == "add_decision_area":
            authority["decision_areas"].append(_decision_area(mutation["decision"], mutation["current_state"], _value(mutation.get("final_decision_maker")), [{"quote": mutation["quote"], "timestamp": mutation["timestamp"]}]))
        else:
            raise AssertionError(f"неизвестная операция ловушки: {op}")


def statuses(result: dict) -> dict[str, str]:
    return {item["id"]: item["status"] for item in result["items"]}


def explain(result: dict) -> str:
    lines = [f"verdict={result['verdict']}"]
    lines += [f"  {item['id']} {item['status']}: {'; '.join(item['reasons'])}" for item in result["items"] if item["status"] != "FULL"]
    lines += [f"  extra {entry['uid']} {entry['status']}: {'; '.join(entry['reasons'])}" for entry in result["extras"]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Фикстура: PASS и порча артефактов
# ---------------------------------------------------------------------------


class EvalFixtureTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.register()
        self.assertEqual(run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "yes", "--masking", "none", "--sensitivity", "internal", "--routes", "local_only", "--retain-source", "yes").returncode, 0)
        self.assertEqual(run(self.root, "speakers", "set", "--meeting", MEETING, "--map", SPEAKER_MAP).returncode, 0)
        self.copy_analysis()

    def evaluate(self, expected_code: int) -> dict:
        completed = run(self.root, "eval", "--meeting", MEETING, "--core", str(FIXTURE_CORE))
        self.assertEqual(completed.returncode, expected_code, completed.stdout + completed.stderr)
        return payload(completed)

    def mutate(self, name: str, change) -> None:
        path = self.meeting / "analysis" / name
        data = read_json(path)
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_fixture_passes(self) -> None:
        result = self.evaluate(0)
        self.assertEqual(result["verdict"], "PASS")
        core_items = [item for item in result["items"] if item["level"] == "core"]
        self.assertGreaterEqual(len(core_items), 7)
        self.assertTrue(all(item["status"] == "FULL" for item in core_items), explain(result))
        self.assertEqual({item["kind"] for item in core_items} >= {"commitment", "risk", "authority", "not_decision", "concession", "agreed_term", "open_question", "outcome"}, True)
        self.assertEqual([entry for entry in result["extras"] if entry["status"] == "DISTORTED"], [])
        self.assertTrue(result["source_matches_core"])
        self.assertNotIn("percent", json.dumps(result["summary"]))
        saved = read_json(self.meeting / "eval_result.json")
        self.assertEqual(saved["verdict"], "PASS")
        text = (self.meeting / "eval_result.md").read_text(encoding="utf-8")
        self.assertIn("**Вердикт: PASS**", text)
        self.assertIn("Общий процент не считается", text)
        # поддерживающие пункты видны, но на вердикт не влияют
        supporting = {item["id"]: item["status"] for item in result["items"] if item["level"] == "supporting"}
        self.assertEqual(supporting.get("S1"), "MISSING")

    def test_removed_commitment_is_missing(self) -> None:
        self.mutate("07_commitments.json", lambda data: data.update(commitments=[]))
        result = self.evaluate(1)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(statuses(result)["K1"], "MISSING", explain(result))

    def test_swapped_executor_is_distorted(self) -> None:
        self.mutate("07_commitments.json", lambda data: data["commitments"][0].update(who="p-zaytseva-polaris"))
        result = self.evaluate(1)
        self.assertEqual(statuses(result)["K1"], "DISTORTED", explain(result))
        self.assertIn("не тот исполнитель", " ".join(next(item for item in result["items"] if item["id"] == "K1")["reasons"]))

    def test_removed_deadline_is_partial(self) -> None:
        self.mutate("07_commitments.json", lambda data: data["commitments"][0].update(deadline=None, deadline_verbatim=None))
        result = self.evaluate(1)
        self.assertEqual(statuses(result)["K1"], "PARTIAL", explain(result))
        self.assertEqual([item["id"] for item in result["items"] if item["level"] == "core" and item["status"] != "FULL"], ["K1"])
        self.assertIn("K1", [entry["id"] for entry in result["human_review"]])

    def test_false_agreement_is_distorted_extra(self) -> None:
        def add_fake(data: dict) -> None:
            fake = copy.deepcopy(data["commitments"][0])
            fake.update(
                id="c-fake-payment", who="p-krylova-polaris", to_whom="p-danilov-nordtech", type="approve_decision", status="agreed",
                what="принять схему оплаты 30/40/30 с первым платежом после обследования", deadline=None, deadline_verbatim=None,
                source_quote="Мы готовы рассмотреть это, но окончательное решение по схеме оплаты я приму после получения уточнённой сметы.", source_timestamp="00:07:47",
            )
            data["commitments"].append(fake)

        self.mutate("07_commitments.json", add_fake)
        result = self.evaluate(1)
        distorted = [entry for entry in result["extras"] if entry["status"] == "DISTORTED"]
        self.assertEqual([entry["id"] for entry in distorted], ["c-fake-payment"], explain(result))
        self.assertIn("K4", distorted[0]["related_items"])
        self.assertEqual(statuses(result)["K4"], "DISTORTED")
        self.assertEqual(statuses(result)["K1"], "FULL")
        self.assertIn("ложное «договорились»", (self.meeting / "eval_result.md").read_text(encoding="utf-8"))

    def test_commitment_to_decide_later_is_not_false_agreement(self) -> None:
        def add_pending(data: dict) -> None:
            pending = copy.deepcopy(data["commitments"][0])
            pending.update(
                id="c-payment-decision", who="p-krylova-polaris", to_whom="p-danilov-nordtech", type="approve_decision",
                what="принять решение по схеме оплаты после получения сметы", deadline="2026-11-10", deadline_verbatim="до десятого ноября",
                source_quote="Решение по схеме оплаты, соответственно, примем до десятого ноября, после сметы.", source_timestamp="00:08:41",
            )
            data["commitments"].append(pending)

        self.mutate("07_commitments.json", add_pending)
        result = self.evaluate(0)
        self.assertEqual(result["verdict"], "PASS", explain(result))
        self.assertEqual(statuses(result)["K4"], "FULL")

    def test_unsupported_quote_is_distorted(self) -> None:
        self.mutate("08_risks.json", lambda data: data["risks"][0]["evidence"][0].update(quote="Этой фразы на встрече не было, но риск записан."))
        result = self.evaluate(1)
        self.assertEqual(statuses(result)["K2"], "DISTORTED", explain(result))
        self.assertIn("цитаты находки нет в источнике", " ".join(next(item for item in result["items"] if item["id"] == "K2")["reasons"]))

    def test_reads_bundle_when_analysis_folder_is_empty(self) -> None:
        self.assertEqual(run(self.root, "bundle", "pack", "--meeting", MEETING).returncode, 0)
        shutil.rmtree(self.meeting / "analysis")
        (self.meeting / "analysis").mkdir()
        result = self.evaluate(0)
        self.assertTrue(any("analysis_bundle.json" in warning for warning in result["warnings"]))

    def test_light_profile_is_flagged_and_does_not_cover_authority(self) -> None:
        review = self.meeting / "review.yaml"
        review.write_text(review.read_text(encoding="utf-8") + "analysis_profile: light\n", encoding="utf-8")
        for name in ("05_power.json", "09_decision_authority.json"):
            (self.meeting / "analysis" / name).unlink()
        result = self.evaluate(1)
        self.assertEqual(statuses(result)["K3"], "MISSING")
        self.assertEqual(statuses(result)["K5"], "MISSING")
        self.assertEqual(statuses(result)["K1"], "FULL")
        self.assertTrue(any("лёгком профиле" in warning for warning in result["warnings"]), result["warnings"])

    def test_usage_errors_exit_2(self) -> None:
        wrong_core = EVALS_DIR / "07-false-agreements" / "core.json"
        mismatch = run(self.root, "eval", "--meeting", MEETING, "--core", str(wrong_core))
        self.assertEqual(mismatch.returncode, 2, mismatch.stdout)
        self.assertIn("не подходит к транскрипту", payload(mismatch)["error"])
        missing = run(self.root, "eval", "--meeting", MEETING, "--core", str(self.tmp / "absent.json"))
        self.assertEqual(missing.returncode, 2)
        broken = self.tmp / "broken-core.json"
        broken.write_text(json.dumps({"items": [{"id": "X", "kind": "commitment", "title": "без исполнителя"}]}), encoding="utf-8")
        self.assertEqual(run(self.root, "eval", "--meeting", MEETING, "--core", str(broken)).returncode, 2)
        for name in ("01_extraction.json", "05_power.json", "07_commitments.json", "08_risks.json", "09_decision_authority.json"):
            (self.meeting / "analysis" / name).unlink()
        empty = run(self.root, "eval", "--meeting", MEETING, "--core", str(FIXTURE_CORE))
        self.assertEqual(empty.returncode, 2)
        self.assertIn("нет артефактов", payload(empty)["error"])


# ---------------------------------------------------------------------------
# Реестр проверочных встреч
# ---------------------------------------------------------------------------


class EvalScenarioRegistryTests(unittest.TestCase):
    def test_registry_structure(self) -> None:
        self.assertGreaterEqual(len(SCENARIOS), 7)
        covered: set[str] = set()
        meeting_ids: set[str] = set()
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.name):
                for name in ("transcript.txt", "meta.json", "core.json"):
                    self.assertTrue((scenario / name).is_file(), f"{scenario.name}: нет {name}")
                meta, core = load(scenario / "meta.json"), load(scenario / "core.json")
                transcript = (scenario / "transcript.txt").read_text(encoding="utf-8")
                self.assertEqual(meta["scenario"], scenario.name)
                self.assertEqual(core["scenario"], scenario.name)
                self.assertIs(meta["synthetic"], True)
                for key in ("title", "checks", "meeting_id", "captured_at", "speaker_map", "expected_speaker_labels", "traps"):
                    self.assertIn(key, meta)
                self.assertTrue(meta["checks"] and meta["traps"])
                self.assertNotIn(meta["meeting_id"], meeting_ids)
                meeting_ids.add(meta["meeting_id"])
                covered |= {check.split(":", 1)[0].strip() for check in meta["checks"]}
                if meta.get("long"):
                    self.assertGreaterEqual(evals.parse_seconds(transcript.strip().splitlines()[-1][1:9]), 60 * 60)
                else:
                    self.assertTrue(2000 <= len(transcript) <= 6000, f"{scenario.name}: {len(transcript)} знаков")
                self.assertEqual(evals.check_core(core, transcript), [])
                self.assertEqual(core["source_sha256"], hashlib.sha256(transcript.encode("utf-8")).hexdigest())
                levels = [item.get("level", "core") for item in core["items"]]
                self.assertGreaterEqual(levels.count("core"), 4)
                core_ids = {item["id"] for item in core["items"]}
                for trap in meta["traps"]:
                    self.assertTrue(set(trap["expect"].get("items", {})) <= core_ids, trap["id"])
                speaker_map = meta["speaker_map"]
                for label in list(speaker_map.get("confirmed", {})) + list(speaker_map.get("unconfirmed", {})):
                    self.assertIn(f"{label}:", transcript)
        self.assertEqual(REQUIRED_CHECKS - covered, set())

    def test_long_meeting_generator_is_deterministic(self) -> None:
        scenario = EVALS_DIR / "08-long-meeting"
        if not (scenario / "generate.py").is_file():
            self.skipTest("нет генератора длинной встречи")
        # без импорта: в папке сценария не появляется __pycache__
        namespace: dict = {"__name__": "long_meeting_generator", "__file__": str(scenario / "generate.py")}
        exec(compile((scenario / "generate.py").read_text(encoding="utf-8"), str(scenario / "generate.py"), "exec"), namespace)
        self.assertEqual(namespace["build"](), (scenario / "transcript.txt").read_text(encoding="utf-8"))

    def test_reference_analysis_passes_and_traps_fail(self) -> None:
        for scenario in SCENARIOS:
            meta, core = load(scenario / "meta.json"), load(scenario / "core.json")
            transcript = (scenario / "transcript.txt").read_text(encoding="utf-8")
            with self.subTest(scenario=scenario.name, trap="reference"):
                artifacts, _ = build_reference(core, meta["meeting_id"])
                result = evals.evaluate(core, transcript, artifacts)
                self.assertEqual(result["verdict"], "PASS", explain(result))
            for trap in meta["traps"]:
                with self.subTest(scenario=scenario.name, trap=trap["id"]):
                    artifacts, where = build_reference(core, meta["meeting_id"])
                    apply_mutations(artifacts, where, trap["mutations"], meta["meeting_id"])
                    result = evals.evaluate(core, transcript, artifacts)
                    expect = trap["expect"]
                    self.assertEqual(result["verdict"], expect["verdict"], explain(result))
                    actual = statuses(result)
                    for item_id, status in expect.get("items", {}).items():
                        self.assertEqual(actual[item_id], status, f"{trap['id']} {item_id}\n{explain(result)}")
                    distorted = [entry for entry in result["extras"] if entry["status"] == "DISTORTED"]
                    self.assertGreaterEqual(len(distorted), expect.get("extras_distorted_min", 0), explain(result))


class EvalScenarioRegistrationTests(WorkspaceTestCase):
    def test_transcripts_register_and_duplicates_are_refused(self) -> None:
        for scenario in SCENARIOS:
            meta = load(scenario / "meta.json")
            with self.subTest(scenario=scenario.name):
                created = run(self.root, "source", "create", "--meeting", meta["meeting_id"], "--transcript", str(scenario / "transcript.txt"), "--captured-at", meta["captured_at"], "--title", meta["title"])
                self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
                result = payload(created)
                self.assertTrue(result["with_timestamps"])
                self.assertEqual(result["speaker_labels"], meta["expected_speaker_labels"])
                for duplicate in meta.get("registration", {}).get("duplicates", []):
                    source = scenario / duplicate["file"]
                    self.assertEqual(source.read_bytes(), (scenario / "transcript.txt").read_bytes())
                    refused = run(self.root, "source", "create", "--meeting", meta["meeting_id"] + "-repeat", "--transcript", str(source))
                    self.assertEqual(refused.returncode, duplicate["expect_exit_code"])
                    self.assertIn(meta["meeting_id"], payload(refused)["error"])
                    self.assertFalse((self.root / "negotiations" / (meta["meeting_id"] + "-repeat")).exists())
                    allowed = run(self.root, "source", "create", "--meeting", meta["meeting_id"] + "-repeat", "--transcript", str(source), "--allow-duplicate")
                    self.assertEqual(allowed.returncode, 0)
                    self.assertEqual(payload(allowed)["duplicate_of"], meta["meeting_id"])

    def test_cli_eval_on_scenario(self) -> None:
        scenario = EVALS_DIR / "07-false-agreements"
        meta, core = load(scenario / "meta.json"), load(scenario / "core.json")
        meeting_id = meta["meeting_id"]
        self.assertEqual(run(self.root, "source", "create", "--meeting", meeting_id, "--transcript", str(scenario / "transcript.txt")).returncode, 0)
        analysis = self.root / "negotiations" / meeting_id / "analysis"
        artifacts, where = build_reference(core, meeting_id)
        for name, data in artifacts.items():
            (analysis / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        passed = run(self.root, "eval", "--meeting", meeting_id, "--core", str(scenario / "core.json"))
        self.assertEqual(passed.returncode, 0, passed.stdout)
        trap = next(item for item in meta["traps"] if item["id"] == "T4")
        apply_mutations(artifacts, where, trap["mutations"], meeting_id)
        (analysis / "07_commitments.json").write_text(json.dumps(artifacts["07_commitments.json"], ensure_ascii=False), encoding="utf-8")
        failed = run(self.root, "eval", "--meeting", meeting_id, "--core", str(scenario / "core.json"))
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertEqual(statuses(payload(failed))["F5"], "DISTORTED")
        self.assertEqual(read_json(self.root / "negotiations" / meeting_id / "eval_result.json")["verdict"], "FAIL")


# ---------------------------------------------------------------------------
# Сопоставление без LLM
# ---------------------------------------------------------------------------


class EvalMatchingUnitTests(unittest.TestCase):
    def test_normalization_and_phrases(self) -> None:
        self.assertEqual(evals.normalize("«Ёлка» — 30/40/30!"), "елка 30 40 30")
        tokens = evals.words("Аванс пятьдесят процентов, 50 на 50")
        self.assertTrue(evals.phrase_in(tokens, "аванс"))
        self.assertTrue(evals.phrase_in(tokens, "пятьдесят процент"))
        self.assertTrue(evals.phrase_in(tokens, "50"))
        self.assertFalse(evals.phrase_in(tokens, "5"))
        self.assertTrue(evals.groups_hit(tokens, [["аванс"], ["50", "тридцат"]]))
        self.assertFalse(evals.groups_hit(tokens, [["аванс"], ["тридцат"]]))

    def test_transliteration_skeleton(self) -> None:
        self.assertEqual(evals.skeleton("Зайцева"), evals.skeleton("zaytseva"))
        self.assertEqual(evals.skeleton("Ерёмин"), evals.skeleton("yeremin"))
        self.assertNotEqual(evals.skeleton("Ворошилова"), evals.skeleton("voroshilin"))
        people = evals.People([{"id": "danilov", "names": ["Олег Данилов"]}, {"id": "voroshilin", "names": ["Глеб Ворошилин"], "present": False}, {"id": "voroshilova", "names": ["Ксения Ворошилова"]}], [])
        self.assertEqual(people.resolve("p-danilov-nordtech"), {"danilov"})
        self.assertEqual(people.resolve("p-voroshilin-yasen"), {"voroshilin"})
        self.assertEqual(people.resolve("Ворошилова"), {"voroshilova"})
        self.assertEqual(people.resolve("p-someone-else"), set())

    def test_claim_detection(self) -> None:
        self.assertTrue(evals.claims_decision_text("Стороны договорились о гарантии три года"))
        self.assertTrue(evals.claims_decision_text("согласовано: монтаж в январе"))
        self.assertFalse(evals.claims_decision_text("решение не принято"))
        self.assertFalse(evals.claims_decision_text("отложено до получения уточнённой сметы"))
        self.assertFalse(evals.claims_decision_text("будет принято после сметы"))
        self.assertFalse(evals.claims_decision_text("согласование окон разгрузки"))

    def test_quote_location(self) -> None:
        transcript = evals.Transcript((FIXTURE / "transcript.txt").read_text(encoding="utf-8"))
        exact = transcript.locate("окончательное решение по схеме оплаты я приму", "00:07:47")
        self.assertEqual((exact.mode, exact.timestamp_ok), ("exact", True))
        drift = transcript.locate("окончательное решение по схеме оплаты я приму", "00:09:30")
        self.assertIs(drift.timestamp_ok, False)
        fuzzy = transcript.locate("Я пришлю смету по трём складам до пятого ноября", "00:08:18")
        self.assertEqual(fuzzy.mode, "fuzzy")
        ellipsis = transcript.locate("Я пришлю уточнённую смету … до пятого ноября", "00:08:18")
        self.assertEqual(ellipsis.mode, "ellipsis")
        self.assertEqual(transcript.locate("Этой фразы не было ни в какой момент встречи").mode, "none")


if __name__ == "__main__":
    unittest.main(verbosity=2)
