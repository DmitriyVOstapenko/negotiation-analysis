#!/usr/bin/env python3
"""Сквозная проверка движка на синтетической встрече (stdlib unittest, без сети и реальных данных).

Запуск: python3 tests/run_tests.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
NA = REPO / "scripts" / "na.py"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic-meeting"
MEETING = "2026-10-28-polaris-nordtech-wms-terms"
ANALYSIS_FILES = [
    "01_extraction.json", "02_tactics.json", "03_manipulations.json", "04_arguments.json", "05_power.json",
    "06_emotions.json", "07_commitments.json", "08_risks.json", "09_decision_authority.json", "10_argument_map.json",
    "11_meeting_effectiveness.json",
]
SPEAKER_MAP = "Speaker 1=Анна Крылова (директор по закупкам);Speaker 2=Олег Данилов (руководитель проектов);Speaker 3=Марина Зайцева (финансовый контролёр)"
# Обязательства синтетической встречи (порядок 07_commitments.json): их — Нордтех, наши — Полярис
THEIR_COMMITMENTS = ["c-2026-10-28-nordtech-estimate", "c-2026-10-28-nordtech-support-cost", "c-2026-10-28-nordtech-warranty-terms"]
OUR_COMMITMENTS = ["c-2026-10-28-polaris-api-docs-request", "c-2026-10-28-polaris-zoning", "c-2026-10-28-polaris-payment-decision"]
FIXTURE_COMMITMENTS = [
    "c-2026-10-28-nordtech-estimate", "c-2026-10-28-polaris-api-docs-request", "c-2026-10-28-polaris-zoning",
    "c-2026-10-28-polaris-payment-decision", "c-2026-10-28-nordtech-support-cost", "c-2026-10-28-nordtech-warranty-terms",
]
OUR_COMMITMENT_WHAT = {
    "c-2026-10-28-polaris-api-docs-request": "запросить у прежнего подрядчика сохранившуюся документацию по API учётной системы",
    "c-2026-10-28-polaris-zoning": "передать Нордтеху схему зонирования распределительного склада — условие уточнённой сметы",
    "c-2026-10-28-polaris-payment-decision": "принять решение по схеме оплаты после получения уточнённой сметы",
}

sys.path.insert(0, str(REPO / "scripts"))
from na_engine.common import read_jsonl, yaml_load  # noqa: E402
from na_engine.schema_runtime import validate_instance  # noqa: E402


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop("NA_WORKSPACE", None)
    return subprocess.run([sys.executable, str(NA), "--root", str(root), "--json", *args], capture_output=True, text=True, encoding="utf-8", env=env, check=False)


def payload(completed: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"Не JSON на stdout:\n{completed.stdout}\n{completed.stderr}") from None


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="na-test-"))
        self.root = self.tmp / "workspace"
        self.root.mkdir()
        self.meeting = self.root / "negotiations" / MEETING

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def register(self, meeting_id: str = MEETING, extra: tuple[str, ...] = ()) -> dict:
        completed = run(self.root, "source", "create", "--meeting", meeting_id, "--transcript", str(FIXTURE / "transcript.txt"), "--provider", "manual", "--captured-at", "2026-10-28T15:00:00+03:00", "--title", "Условия внедрения складского учёта", *extra)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return payload(completed)

    def copy_analysis(self) -> None:
        for name in ANALYSIS_FILES:
            shutil.copyfile(FIXTURE / "analysis" / name, self.meeting / "analysis" / name)

    def prepare_valid_meeting(self) -> None:
        self.register()
        self.assertEqual(run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "yes", "--masking", "none", "--sensitivity", "internal", "--routes", "local_only", "--retain-source", "yes").returncode, 0)
        self.assertEqual(run(self.root, "speakers", "set", "--meeting", MEETING, "--map", SPEAKER_MAP).returncode, 0)
        self.copy_analysis()
        self.assertEqual(run(self.root, "validate", "--meeting", MEETING).returncode, 0)


class EndToEndTests(WorkspaceTestCase):
    def test_full_path(self) -> None:
        # 1. Источник → SourcePacket
        created = self.register()
        packet = read_json(self.meeting / "source_packet.json")
        self.assertEqual(packet["provider"], "manual")
        self.assertEqual(packet["processing_status"], "registered")
        self.assertEqual(packet["sensitivity"], "unknown")
        self.assertEqual((packet["consent"], packet["masking_mode"], packet["allowed_routes"], packet["retention_decision"]), ("not_set",) * 4)
        self.assertEqual(packet["speaker_labels"], ["Speaker 1", "Speaker 2", "Speaker 3"])
        self.assertTrue(packet["has_timestamps"])
        self.assertEqual(packet["segments"][0]["timestamp"], "00:00:04")
        self.assertEqual(packet["segments"][0]["speaker"], "Speaker 1")
        self.assertEqual(len(packet["content_sha256"]), 64)
        self.assertEqual((self.meeting / "transcript.txt").read_bytes(), (FIXTURE / "transcript.txt").read_bytes())
        review = yaml_load((self.meeting / "review.yaml").read_text(encoding="utf-8"))
        self.assertEqual(review["processing_gate"]["consent"], "not_set")
        # исключённый фрагмент: начало и конец (None — до конца записи), как 01.excluded_source_range
        self.assertEqual({key: review["source_scope"][key] for key in ("excluded_from", "excluded_until", "exclusion_reason")}, dict.fromkeys(("excluded_from", "excluded_until", "exclusion_reason")))
        meta = yaml_load((self.meeting / "meta.yaml").read_text(encoding="utf-8"))
        self.assertEqual(meta["meeting"]["id"], MEETING)
        self.assertEqual(meta["source"]["content_sha256"], packet["content_sha256"])
        self.assertEqual(created["segments"], 63)

        # 1a. Дедупликация
        duplicate = run(self.root, "source", "create", "--meeting", "other-meeting", "--transcript", str(FIXTURE / "transcript.txt"))
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn(MEETING, payload(duplicate)["error"])
        self.assertFalse((self.root / "negotiations" / "other-meeting").exists())

        # 2. Шлюз
        gate = run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "yes", "--masking", "none", "--sensitivity", "internal", "--routes", "local_only", "--retain-source", "yes")
        self.assertEqual(gate.returncode, 0, gate.stderr)
        self.assertEqual(payload(gate)["processing_status"], "gate_passed")
        packet = read_json(self.meeting / "source_packet.json")
        self.assertEqual(packet["processing_status"], "gate_passed")
        self.assertEqual(packet["sensitivity"], "internal")
        review = yaml_load((self.meeting / "review.yaml").read_text(encoding="utf-8"))
        self.assertEqual(review["processing_gate"]["source"], "user_confirmation")
        self.assertEqual(review["processing_gate"]["retention_decision"], "retain")

        # 3. Спикеры
        speakers = run(self.root, "speakers", "set", "--meeting", MEETING, "--map", SPEAKER_MAP)
        self.assertEqual(speakers.returncode, 0, speakers.stderr)
        review = yaml_load((self.meeting / "review.yaml").read_text(encoding="utf-8"))
        self.assertEqual(review["speaker_mapping"]["status"], "confirmed")
        self.assertEqual(review["speaker_mapping"]["source"], "user_confirmation")
        self.assertEqual({item["label"]: item["person_name"] for item in review["speaker_mapping"]["speakers"]}["Speaker 2"], "Олег Данилов")
        self.assertEqual(read_json(self.meeting / "source_packet.json")["speaker_map"]["Speaker 3"], "Марина Зайцева")

        # 4. 11 JSON → validate (valid)
        self.copy_analysis()
        validate = run(self.root, "validate", "--meeting", MEETING)
        self.assertEqual(validate.returncode, 0, validate.stdout)
        report = payload(validate)
        self.assertTrue(report["valid"])
        self.assertTrue(report["schema_valid"])
        self.assertEqual(report["schema_version"], "1.1.0")
        self.assertEqual(report["checked_quotes"], 35)
        self.assertEqual([item["id"] for item in read_json(self.meeting / "analysis" / "07_commitments.json")["commitments"]], FIXTURE_COMMITMENTS)
        self.assertTrue((self.meeting / "analysis" / "validation_report.json").is_file())

        # 5. Порча цитаты → validate (invalid, exit 1)
        facts_path = self.meeting / "analysis" / "01_extraction.json"
        original = facts_path.read_text(encoding="utf-8")
        broken = json.loads(original)
        broken["key_facts"][0]["quote"] = "Этой фразы нет в транскрипте."
        facts_path.write_text(json.dumps(broken, ensure_ascii=False, indent=2), encoding="utf-8")
        invalid = run(self.root, "validate", "--meeting", MEETING)
        self.assertEqual(invalid.returncode, 1)
        self.assertFalse(payload(invalid)["valid"])
        self.assertTrue(any("quote not found in transcript" in item for item in payload(invalid)["errors"]))
        self.assertFalse(read_json(self.meeting / "analysis" / "validation_report.json")["valid"])
        facts_path.write_text(original, encoding="utf-8")
        self.assertEqual(run(self.root, "validate", "--meeting", MEETING).returncode, 0)

        # 6. Отчёт с блоком «Один экран»
        report_run = run(self.root, "report", "--meeting", MEETING)
        self.assertEqual(report_run.returncode, 0, report_run.stdout)
        text = (self.meeting / "report_auto.md").read_text(encoding="utf-8")
        self.assertTrue(text.index("## Один экран") < text.index("## Результат"))
        for heading in ("### Главный вывод", "### Требуемое решение", "### Ключевые факты", "### Риски", "### Варианты", "### Следующий шаг", "## Контроль качества"):
            self.assertIn(heading, text)
        for needle in ("Анна Крылова", "Олег Данилов", "Марина Зайцева", "прислать уточнённую смету по трём складам", "отсутствия документации по API", "3.3 из 5", *OUR_COMMITMENT_WHAT.values()):
            self.assertIn(needle, text)
        report_check = read_json(self.meeting / "analysis" / "report_auto_validation.json")
        self.assertTrue(report_check["valid"])
        self.assertEqual(report_check["commitments_checked"], 6)

        # 7. Follow-up — всегда draft_not_sent; в нём обязательства обеих сторон
        followup = run(self.root, "followup", "--meeting", MEETING)
        self.assertEqual(followup.returncode, 0, followup.stdout)
        bundle = read_json(self.meeting / "follow_up.json")
        self.assertEqual(bundle["delivery_status"], "draft_not_sent")
        self.assertEqual([item["id"] for item in bundle["commitments"]], FIXTURE_COMMITMENTS)
        self.assertEqual({item["who"] for item in bundle["commitments"] if item["id"] in OUR_COMMITMENTS}, {"p-krylova-polaris", "p-zaytseva-polaris"})
        protocol = (self.meeting / "follow_up_protocol.md").read_text(encoding="utf-8")
        self.assertIn("draft_not_sent", protocol)
        for what in OUR_COMMITMENT_WHAT.values():
            self.assertIn(what, protocol)
        self.assertIn("Не отправлено", (self.meeting / "follow_up_message.md").read_text(encoding="utf-8"))

        # 8. knowledge diff — ничего не применяет
        (self.root / "knowledge" / "people").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE / "existing_p-krylova-polaris.md", self.root / "knowledge" / "people" / "p-krylova-polaris.md")
        shutil.copyfile(FIXTURE / "knowledge_proposal.json", self.meeting / "knowledge_proposal.json")
        diff = run(self.root, "knowledge", "diff", "--meeting", MEETING)
        self.assertEqual(diff.returncode, 0, diff.stdout)
        diff_text = (self.meeting / "knowledge_diff.md").read_text(encoding="utf-8")
        self.assertIn("`proposed`", diff_text)
        self.assertIn("+++ b/knowledge/people/p-danilov-nordtech.md", diff_text)
        for commitment_id in FIXTURE_COMMITMENTS:  # обязательства обеих сторон — в diff до записи
            self.assertIn(f"| knowledge/commitments/open.jsonl | append | {commitment_id} |", diff_text)
        self.assertIn("- Записей реестров: добавить 8, обновить 0, закрыть 0", diff_text)
        self.assertFalse((self.root / "knowledge" / "people" / "p-danilov-nordtech.md").exists())
        self.assertEqual(read_jsonl(self.root / "knowledge" / "commitments" / "open.jsonl"), [])

        # 9. apply без --confirm — отказ
        refused = run(self.root, "knowledge", "apply", "--meeting", MEETING)
        self.assertEqual(refused.returncode, 2)
        self.assertFalse((self.root / "knowledge" / "people" / "p-danilov-nordtech.md").exists())
        self.assertEqual(read_jsonl(self.root / "knowledge" / "commitments" / "open.jsonl"), [])

        # 10. apply --confirm
        applied = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        result = payload(applied)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(result["files_written"]), 4)
        krylova = (self.root / "knowledge" / "people" / "p-krylova-polaris.md").read_text(encoding="utf-8")
        self.assertIn("(m) Предпочитает получать смету до внутреннего бюджетного комитета.", krylova)
        self.assertIn("## Полномочия и влияние", krylova)
        self.assertTrue((self.root / "knowledge" / "organizations" / "org-nordtech.md").is_file())
        self.assertTrue((self.root / "knowledge" / "topics" / "wms-three-warehouses.md").is_file())
        open_commitments = read_jsonl(self.root / "knowledge" / "commitments" / "open.jsonl")
        self.assertEqual([item["id"] for item in open_commitments], FIXTURE_COMMITMENTS)
        analysis_commitments = {item["id"]: item for item in read_json(FIXTURE / "analysis" / "07_commitments.json")["commitments"]}
        self.assertEqual({item["id"]: item for item in open_commitments}, analysis_commitments, "в реестре — те же записи, что в 07")
        self.assertEqual([item["id"] for item in read_jsonl(self.root / "knowledge" / "risks" / "active.jsonl")], ["r-2026-10-28-api-documentation"])
        self.assertEqual([item["id"] for item in read_jsonl(self.root / "knowledge" / "decisions" / "register.jsonl")], ["d-2026-10-28-payment-scheme"])
        ledger = read_jsonl(self.root / "knowledge" / "events" / "ledger.jsonl")
        self.assertEqual(len(ledger), 7)  # шесть обязательств и один риск
        self.assertEqual(sorted(event["entity_id"] for event in ledger if event["entity_type"] == "commitment"), sorted(FIXTURE_COMMITMENTS))
        schema = read_json(REPO / "core" / "contracts" / "knowledge_event.schema.json")
        for event in ledger:
            self.assertEqual(validate_instance(event, schema, schema), [])
        self.assertIn("`applied`", (self.meeting / "knowledge_diff.md").read_text(encoding="utf-8"))
        receipt = read_json(self.meeting / "knowledge_apply.json")
        self.assertEqual(len(receipt["applied_proposals"]), 1)

        # 11. Повторный apply — идемпотентен
        again = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        self.assertEqual(again.returncode, 0)
        self.assertEqual(payload(again)["status"], "already_applied")
        self.assertEqual(len(read_jsonl(self.root / "knowledge" / "events" / "ledger.jsonl")), 7)
        self.assertEqual(len(read_jsonl(self.root / "knowledge" / "commitments" / "open.jsonl")), 6)
        self.assertEqual(len(read_json(self.meeting / "knowledge_apply.json")["applied_proposals"]), 1)

        # 12. refresh-all (финальный report.md имитирует редакцию человека)
        shutil.copyfile(self.meeting / "report_auto.md", self.meeting / "report.md")
        refreshed = run(self.root, "refresh-all")
        self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
        run_result = payload(refreshed)
        self.assertTrue(run_result["valid"])
        manifest = read_json(self.meeting / "run_manifest.json")
        self.assertEqual(manifest["overall_status"], "ready")
        self.assertIsNone(manifest["resume_from"])
        self.assertEqual(manifest["contracts"]["analysis_schema_version"], "1.1.0")
        self.assertTrue(read_json(self.root / "system" / "event_projection_validation.json")["valid"])
        self.assertTrue((self.root / "system" / "search.sqlite").is_file())
        self.assertTrue((self.root / "system" / "pipeline_run.json").is_file())
        self.assertIn("generated by na.py", (self.root / "knowledge" / "index.md").read_text(encoding="utf-8"))

        # 12a. Повторный refresh-all переиспользует готовую встречу
        second = payload(run(self.root, "refresh-all"))
        self.assertEqual(second["meetings"][0]["status"], "reused")

        # 13. Контрольный центр содержит обязательства обеих сторон и риск
        control = run(self.root, "control-center")
        self.assertEqual(control.returncode, 0, control.stdout)
        center = (self.root / "system" / "control_center.md").read_text(encoding="utf-8")
        self.assertIn("прислать уточнённую смету по трём складам", center)
        self.assertIn("отсутствия документации по API", center)
        for what in OUR_COMMITMENT_WHAT.values():
            self.assertIn(what, center)
        state = read_json(self.root / "system" / "control_center.json")
        self.assertEqual(state["summary"]["data_errors"], 0)
        self.assertEqual(state["summary"]["open_commitments"], 6)
        owners = {row["id"]: row["owner_name"] for row in state["commitments"]["open"]}
        self.assertEqual(sorted(owners), sorted(FIXTURE_COMMITMENTS))
        self.assertEqual(owners["c-2026-10-28-polaris-api-docs-request"], "Анна Крылова")
        self.assertEqual(owners["c-2026-10-28-polaris-payment-decision"], "Анна Крылова")
        self.assertEqual(owners["c-2026-10-28-polaris-zoning"], "Марина Зайцева")  # карточки нет — имя из участников встречи
        self.assertEqual(state["summary"]["active_risks"], 1)
        self.assertEqual(state["meetings"][0]["pipeline_status"], "ready")

        # 14. Очередь и решение пользователя
        queue = read_json(self.root / "system" / "review_queue.json")
        open_before = queue["summary"]["open"]
        item = next(entry for entry in queue["items"] if entry["category"] == "risk_deadline")
        recorded = run(self.root, "review", "record", "--item", item["item_id"], "--decision", "Срок митигации 2026-11-20 после ответа подрядчика", "--status", "deferred")
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        decisions = read_jsonl(self.root / "knowledge" / "review_decisions.jsonl")
        self.assertEqual(decisions[-1]["source"], "user_confirmation")
        self.assertEqual(decisions[-1]["item_id"], item["item_id"])
        queue_after = read_json(self.root / "system" / "review_queue.json")
        self.assertEqual(queue_after["summary"]["open"], open_before - 1)
        self.assertEqual(next(entry for entry in queue_after["items"] if entry["item_id"] == item["item_id"])["status"], "deferred")
        # запись решения не меняет сам реестр рисков
        self.assertIsNone(read_jsonl(self.root / "knowledge" / "risks" / "active.jsonl")[0]["mitigation"]["deadline"])
        unknown = run(self.root, "review", "record", "--item", "rq-0000000000000000", "--decision", "x")
        self.assertEqual(unknown.returncode, 2)

        # 15. Поиск
        found = payload(run(self.root, "search", "документация API"))
        self.assertGreaterEqual(found["count"], 1)
        self.assertTrue(any(item["kind"] == "transcript_quote" for item in found["results"]))

        # 16. Статус
        status = payload(run(self.root, "status", "--meeting", MEETING))
        self.assertEqual(status["overall_status"], "ready")


class GateAndInvariantTests(WorkspaceTestCase):
    def test_consent_no_keeps_source_blocked(self) -> None:
        self.register()
        gate = run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "no", "--masking", "full", "--sensitivity", "regulated", "--routes", "local_only")
        self.assertEqual(gate.returncode, 0)
        self.assertEqual(payload(gate)["processing_status"], "blocked_by_gate")
        self.assertEqual(read_json(self.meeting / "source_packet.json")["processing_status"], "blocked_by_gate")
        manifest = payload(run(self.root, "manifest", "--meeting", MEETING))
        self.assertEqual(manifest["resume_from"], "privacy_gate_passed")
        self.assertEqual(manifest["overall_status"], "awaiting_work")
        queue = payload(run(self.root, "review", "queue"))
        self.assertGreaterEqual(queue["summary"]["open"], 1)

    def test_manual_marks_are_protected(self) -> None:
        self.prepare_valid_meeting()
        shutil.copyfile(FIXTURE / "existing_p-krylova-polaris.md", self.root / "knowledge" / "people" / "p-krylova-polaris.md")
        proposal = read_json(FIXTURE / "knowledge_proposal.json")
        for entry in proposal["files"]:
            if entry["path"].endswith("p-krylova-polaris.md"):
                entry["content"] = entry["content"].replace("- (m) Предпочитает получать смету до внутреннего бюджетного комитета.\n", "")
        (self.meeting / "knowledge_proposal.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        diff = run(self.root, "knowledge", "diff", "--meeting", MEETING)
        self.assertEqual(diff.returncode, 1)
        self.assertTrue(any("(m)" in error for error in payload(diff)["errors"]))
        apply = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        self.assertNotEqual(apply.returncode, 0)
        self.assertIn("(m) Предпочитает", (self.root / "knowledge" / "people" / "p-krylova-polaris.md").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "knowledge" / "people" / "p-danilov-nordtech.md").exists())

    def test_apply_stops_when_target_changed_after_diff(self) -> None:
        # другой чат или ручная правка изменили карточку после показа diff — apply ничего не пишет
        self.prepare_valid_meeting()
        person = self.root / "knowledge" / "people" / "p-krylova-polaris.md"
        shutil.copyfile(FIXTURE / "existing_p-krylova-polaris.md", person)
        shutil.copyfile(FIXTURE / "knowledge_proposal.json", self.meeting / "knowledge_proposal.json")
        self.assertEqual(run(self.root, "knowledge", "diff", "--meeting", MEETING).returncode, 0)
        self.assertTrue((self.meeting / "knowledge_diff_snapshot.json").exists())
        edited = person.read_text(encoding="utf-8") + "- (m) Правка из другого чата после показа diff.\n"
        person.write_text(edited, encoding="utf-8")
        apply = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        self.assertNotEqual(apply.returncode, 0)
        self.assertIn("изменились файлы базы", apply.stderr + apply.stdout)
        self.assertEqual(person.read_text(encoding="utf-8"), edited)
        self.assertFalse((self.root / "knowledge" / "people" / "p-danilov-nordtech.md").exists())
        self.assertFalse((self.meeting / "knowledge_apply.json").exists())

    def test_close_moves_record_without_losing_history(self) -> None:
        self.prepare_valid_meeting()
        shutil.copyfile(FIXTURE / "existing_p-krylova-polaris.md", self.root / "knowledge" / "people" / "p-krylova-polaris.md")
        shutil.copyfile(FIXTURE / "knowledge_proposal.json", self.meeting / "knowledge_proposal.json")
        self.assertEqual(run(self.root, "knowledge", "diff", "--meeting", MEETING).returncode, 0)
        self.assertEqual(run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm").returncode, 0)
        ledger_before = len(read_jsonl(self.root / "knowledge" / "events" / "ledger.jsonl"))

        # update со статусом закрытия запрещён — только action=close
        bad = {"meeting_id": MEETING, "generated_at": "2026-11-06T10:00:00+03:00", "contract_version": "1.1.0", "files": [], "records": [
            {"registry": "knowledge/risks/active.jsonl", "action": "update", "id": "r-2026-10-28-api-documentation", "record": {"status": "closed"}}]}
        (self.meeting / "knowledge_proposal.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(run(self.root, "knowledge", "diff", "--meeting", MEETING).returncode, 1)
        self.assertEqual(read_jsonl(self.root / "knowledge" / "risks" / "active.jsonl")[0]["status"], "open")

        good = {"meeting_id": MEETING, "generated_at": "2026-11-06T10:00:00+03:00", "contract_version": "1.1.0", "files": [], "records": [
            {"registry": "knowledge/commitments/open.jsonl", "action": "close", "id": "c-2026-10-28-nordtech-estimate", "record": {"status": "done", "follow_up": {"reminder_date": "2026-11-03", "reminder_sent": False, "actual_completion_date": "2026-11-04", "completion_notes": "смета получена"}}}]}
        (self.meeting / "knowledge_proposal.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        # apply без свежего diff отклоняется
        self.assertEqual(run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm").returncode, 2)
        self.assertEqual(run(self.root, "knowledge", "diff", "--meeting", MEETING).returncode, 0)
        self.assertIn("| knowledge/commitments/open.jsonl | close | c-2026-10-28-nordtech-estimate |", (self.meeting / "knowledge_diff.md").read_text(encoding="utf-8"))
        self.assertEqual(run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm").returncode, 0)
        # закрыта только смета; остальные обязательства, в том числе наши, остаются открытыми
        self.assertEqual([item["id"] for item in read_jsonl(self.root / "knowledge" / "commitments" / "open.jsonl")], [item for item in FIXTURE_COMMITMENTS if item != "c-2026-10-28-nordtech-estimate"])
        closed = read_jsonl(self.root / "knowledge" / "commitments" / "closed.jsonl")
        self.assertEqual([item["id"] for item in closed], ["c-2026-10-28-nordtech-estimate"])
        self.assertEqual(closed[0]["status"], "done")
        self.assertEqual(closed[0]["closed_source"], "user_confirmation")
        self.assertEqual(closed[0]["source_quote"], "Я пришлю уточнённую смету по трём складам до пятого ноября, при условии что схему зонирования мы получим на этой неделе.")
        ledger = read_jsonl(self.root / "knowledge" / "events" / "ledger.jsonl")
        self.assertEqual(len(ledger), ledger_before + 1)
        self.assertEqual(ledger[-1]["target_view"], "closed")
        self.assertEqual(run(self.root, "report", "--meeting", MEETING).returncode, 0)
        shutil.copyfile(self.meeting / "report_auto.md", self.meeting / "report.md")
        self.assertEqual(run(self.root, "refresh-all").returncode, 0)
        self.assertTrue(read_json(self.root / "system" / "event_projection_validation.json")["valid"])

    def test_bundle_pack_and_unpack(self) -> None:
        self.prepare_valid_meeting()
        self.assertEqual(run(self.root, "manifest", "--meeting", MEETING).returncode, 0)
        packed = run(self.root, "bundle", "pack", "--meeting", MEETING)
        self.assertEqual(packed.returncode, 0, packed.stdout)
        bundle = read_json(self.meeting / "analysis_bundle.json")
        self.assertEqual(bundle["meeting_id"], MEETING)
        self.assertEqual(bundle["contract_version"], "1.1.0")
        for name in ANALYSIS_FILES:
            self.assertIn(f"analysis/{name}", bundle["files"])
        self.assertIn("source_packet.json", bundle["files"])
        self.assertIn("run_manifest.json", bundle["files"])
        self.assertEqual(bundle["files"]["analysis/09_decision_authority.json"]["view_type"], "decision_authority_map")
        self.assertEqual([item["id"] for item in bundle["files"]["analysis/07_commitments.json"]["commitments"]], FIXTURE_COMMITMENTS)

        unpacked = payload(run(self.root, "bundle", "unpack", "--meeting", MEETING))
        self.assertEqual(unpacked["written"], [])
        self.assertEqual(len(unpacked["unchanged"]), len(bundle["files"]))

        target = self.meeting / "analysis" / "03_manipulations.json"
        edited = read_json(target)
        edited["conclusion"] += " (правка после упаковки)"
        time.sleep(1.1)
        target.write_text(json.dumps(edited, ensure_ascii=False, indent=2), encoding="utf-8")
        refused = run(self.root, "bundle", "unpack", "--meeting", MEETING)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual([item["path"] for item in payload(refused)["skipped"]], ["analysis/03_manipulations.json"])
        self.assertIn("правка после упаковки", target.read_text(encoding="utf-8"))
        forced = run(self.root, "bundle", "unpack", "--meeting", MEETING, "--force")
        self.assertEqual(forced.returncode, 0)
        self.assertEqual(payload(forced)["written"], ["analysis/03_manipulations.json"])
        self.assertNotIn("правка после упаковки", target.read_text(encoding="utf-8"))

        # пакет прежней версии контрактов (1.0.0, без необязательных полей 1.1.0) читается как есть
        old = read_json(self.meeting / "analysis_bundle.json")
        old["contract_version"] = "1.0.0"
        (self.meeting / "analysis_bundle.json").write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        target.unlink()
        restored = run(self.root, "bundle", "unpack", "--meeting", MEETING)
        self.assertEqual(restored.returncode, 0, restored.stdout)
        self.assertEqual(payload(restored)["written"], ["analysis/03_manipulations.json"])
        self.assertEqual(run(self.root, "validate", "--meeting", MEETING).returncode, 0)

    def test_audio_module_reports_missing_dependencies_without_crashing(self) -> None:
        from na_engine import audio_acoustic

        report = audio_acoustic.dependency_report()
        self.assertEqual(set(report["required"]), {"numpy", "soundfile", "librosa"})
        completed = run(self.root, "audio", "--check")
        self.assertIn(completed.returncode, (0, 2))
        self.assertEqual(payload(completed)["available"], completed.returncode == 0)

    def test_workspace_root_from_environment(self) -> None:
        env = {**os.environ, "NA_WORKSPACE": str(self.root), "PYTHONUTF8": "1"}
        completed = subprocess.run([sys.executable, str(NA), "--json", "status"], capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["root"], str(self.root.resolve()))


class NegociarRuTests(unittest.TestCase):
    """Встроенный навык negociar-ru: канон собран, поиск работает, изъятое не распространяется."""

    SKILL = REPO / "skills" / "negociar-ru"
    BUSCAR = SKILL / "scripts" / "buscar.py"

    def run_buscar(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO), "PYTHONUTF8": "1"}
        return subprocess.run([sys.executable, str(self.BUSCAR), *args], capture_output=True, text=True,
                              encoding="utf-8", env=env, check=False)

    def test_canon_complete(self) -> None:
        canon = self.SKILL / "data" / "canon"
        files = sorted(p for p in canon.glob("[01][0-9]-*.md") if p.name != "00-ARQUITECTURA.md")
        self.assertEqual(len(files), 10)
        total = sum(len([l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("### ")]) for p in files)
        self.assertEqual(total, 275)

    def test_library_integrity(self) -> None:
        casos = json.loads((self.SKILL / "data" / "casos.json").read_text(encoding="utf-8"))
        self.assertEqual(len(casos), 1019)
        self.assertEqual(len({(c["id"], c["n"]) for c in casos}), 1019)
        self.assertTrue(all(c.get("translation_status") for c in casos))

    def test_search_russian_word_forms(self) -> None:
        # формы запроса отличаются от заголовка («извинения» / «извинений», «фиксация» / «фиксацию»);
        # третье слово держит случай в топе по мере роста переведённой библиотеки
        out = self.run_buscar("требовать извинения фиксация", "--n", "3")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Требовать извинений как фиксацию", out.stdout)
        canon = self.run_buscar("--canon", "D8")
        self.assertEqual(canon.returncode, 0)
        self.assertTrue(canon.stdout.strip().endswith("08-gestion-conflicto-conflict-management.md"))

    def test_restored_material_with_adaptation_note(self) -> None:
        # изъятие 05 #8 и случая 400 отменено автором адаптации; текст полный, с примечанием адаптации,
        # а правила навыка запрещают советнику рекомендовать подкуп
        canon05 = (self.SKILL / "data" / "canon" / "05-ventas-complejas-complex-sales.md").read_text(encoding="utf-8")
        self.assertNotIn("изъят из русской адаптации", canon05)
        self.assertIn("квадроцикл", canon05)
        self.assertIn("Примечание адаптации", canon05)
        casos = json.loads((self.SKILL / "data" / "casos.json").read_text(encoding="utf-8"))
        c400 = next(c for c in casos if c["n"] == 400)
        self.assertNotEqual(c400["translation_status"], "excluded")
        self.assertIn("квадроцикл", c400["body"])
        skill = (self.SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("не рекомендуй подкуп", skill)

    def test_attribution_present(self) -> None:
        notice = (REPO / "licenses" / "negociar-ru" / "NOTICE.md").read_text(encoding="utf-8")
        for needle in ("Artur Galiev", "Arti Galiev", "d94df3bbb00e02e9389a7fa45f54d4907d318743", "CC BY-NC-SA 4.0", "не заявлял об"):
            self.assertIn(needle, notice)
        self.assertTrue((REPO / "licenses" / "negociar-ru" / "UPSTREAM-LICENSE-ORIGINAL").exists())


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    """Подключить тесты из tests/test_*.py (каждая область — свой файл; общие помощники берутся отсюда)."""
    import importlib

    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    for path in sorted(here.glob("test_*.py")):
        standard_tests.addTests(loader.loadTestsFromModule(importlib.import_module(path.stem)))
    return standard_tests


if __name__ == "__main__":
    unittest.main(verbosity=2)
