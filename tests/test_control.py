"""Область C: зависшие вопросы в контрольном центре, версия схемы и миграции, ревью контрагента.

Подхватывается tests/run_tests.py автоматически. Данные — синтетическая встреча из фикстуры
и вымышленные дополнения в самих тестах.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from run_tests import FIXTURE, MEETING, NA, WorkspaceTestCase, payload, read_json, run


SECOND = "2026-11-12-polaris-nordtech-followup"
TOPIC = "wms-three-warehouses"
ESTIMATE = "c-2026-10-28-nordtech-estimate"
SUPPORT_COST = "c-2026-10-28-nordtech-support-cost"
WARRANTY = "c-2026-10-28-nordtech-warranty-terms"
ZONING = "c-2026-10-28-polaris-zoning"
API_DOCS = "c-2026-10-28-polaris-api-docs-request"
PAYMENT_DECISION = "c-2026-10-28-polaris-payment-decision"
WORKSPACE = Path("knowledge") / "workspace.json"


def plain(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop("NA_WORKSPACE", None)
    return subprocess.run([sys.executable, str(NA), "--root", str(root), *args], capture_output=True, text=True, encoding="utf-8", env=env, check=False)


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def proposal(meeting_id: str, records: list[dict], files: list[dict] | None = None) -> dict:
    return {"meeting_id": meeting_id, "generated_at": "2026-11-12T18:00:00+03:00", "contract_version": "1.0.0", "files": files or [], "records": records}


class ControlAreaTestCase(WorkspaceTestCase):
    def apply_proposal(self, meeting_id: str, content: dict) -> None:
        (self.root / "negotiations" / meeting_id / "knowledge_proposal.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        diff = run(self.root, "knowledge", "diff", "--meeting", meeting_id)
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)
        applied = run(self.root, "knowledge", "apply", "--meeting", meeting_id, "--confirm")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)

    def base_with_fixture_applied(self) -> None:
        self.prepare_valid_meeting()
        shutil.copyfile(FIXTURE / "existing_p-krylova-polaris.md", self.root / "knowledge" / "people" / "p-krylova-polaris.md")
        self.apply_proposal(MEETING, read_json(FIXTURE / "knowledge_proposal.json"))

    def add_second_meeting(self) -> None:
        created = run(self.root, "source", "create", "--meeting", SECOND, "--transcript", str(FIXTURE / "transcript.txt"), "--captured-at", "2026-11-12T15:00:00+03:00", "--title", "Повторная встреча по складскому учёту", "--allow-duplicate")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        analysis = self.root / "negotiations" / SECOND / "analysis"
        doc = {"meeting_id": SECOND, "commitments": [], "open_questions": [
            {"topic": TOPIC, "question": "Условия гарантии на оборудование", "quote": "По гарантии на оборудование ответа от поставщика пока нет.", "timestamp": "00:12:40"}]}
        (analysis / "07_commitments.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def control_center(self, as_of: str) -> dict:
        completed = run(self.root, "control-center", "--as-of", as_of)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return read_json(self.root / "system" / "control_center.json")

    def queue_categories(self) -> dict[str, str]:
        queue = payload(run(self.root, "review", "queue"))
        self.assertTrue(queue["valid"])
        items = read_json(self.root / "system" / "review_queue.json")["items"]
        return {item["category"]: item["status"] for item in items}


class StalledItemsTests(ControlAreaTestCase):
    def test_stalled_topic_appears_on_second_meeting_and_clears_after_decision(self) -> None:
        self.base_with_fixture_applied()
        self.assertEqual(self.control_center("2026-11-20")["stalled"]["topics"], [])

        self.add_second_meeting()
        state = self.control_center("2026-11-20")
        topics = {row["key"]: row for row in state["stalled"]["topics"]}
        self.assertIn(TOPIC, topics)
        row = topics[TOPIC]
        self.assertEqual(row["meetings_count"], 2)
        self.assertEqual([meeting["meeting_id"] for meeting in row["meetings"]], [MEETING, SECOND])
        self.assertEqual(row["register_decisions"], [])
        self.assertEqual(row["days_open"], 23)
        self.assertEqual(row["meetings"][0]["signals"][0]["quote"], "Вопрос по гарантии на оборудование остаётся открытым, его обсудим на следующей встрече.")
        self.assertEqual(state["summary"]["stalled_topics"], 1)
        center = (self.root / "system" / "control_center.md").read_text(encoding="utf-8")
        self.assertIn("### Зависшие вопросы", center)
        self.assertIn("Не путать с просрочкой", center)
        self.assertIn("Внедрение складского учёта на три склада", center)
        self.assertEqual(self.queue_categories().get("stalled_topic"), "open")

        # решение по теме в реестре — через проект изменений и подтверждение
        decision = {
            "registry": "knowledge/decisions/register.jsonl", "action": "append", "id": "d-2026-11-12-warranty",
            "record": {"id": "d-2026-11-12-warranty", "statement": "Гарантия на оборудование — по условиям поставщика, 24 месяца", "status": "confirmed",
                       "decision_owner": "p-krylova-polaris", "decided_at": "2026-11-12", "meeting_id": SECOND, "linked_topic": TOPIC,
                       "evidence": [{"source_id": SECOND, "evidence_class": "user_confirmed_decision", "quote": None, "timestamp": None, "confidence": "high"}]},
        }
        self.apply_proposal(SECOND, proposal(SECOND, [decision]))
        state = self.control_center("2026-11-20")
        self.assertEqual(state["stalled"]["topics"], [])
        self.assertIn("Зависших вопросов нет", (self.root / "system" / "control_center.md").read_text(encoding="utf-8"))
        self.assertNotIn("stalled_topic", self.queue_categories())

    def test_deadline_moved_twice_is_listed_apart_from_overdue(self) -> None:
        self.base_with_fixture_applied()
        moves = (("2026-11-12", "исполнитель не получил схему зонирования"), ("2026-11-19", "поставщик терминалов пересматривает цены"))
        for index, (deadline, reason) in enumerate(moves):
            update = {"registry": "knowledge/commitments/open.jsonl", "action": "update", "id": ESTIMATE, "record": {"deadline": deadline, "change_reason": reason}}
            self.apply_proposal(MEETING, proposal(MEETING, [update]))
            if index == 0:
                self.assertEqual(self.control_center("2026-11-15")["stalled"]["rescheduled_commitments"], [], "один перенос — ещё не зависание")

        state = self.control_center("2026-11-15")
        rows = state["stalled"]["rescheduled_commitments"]
        self.assertEqual([row["id"] for row in rows], [ESTIMATE])
        self.assertEqual(rows[0]["reschedules"], 2)
        self.assertEqual(rows[0]["chain"], ["2026-11-05", "2026-11-12", "2026-11-19"])
        self.assertEqual(rows[0]["basis"], "журнал событий")
        self.assertEqual([change.get("meeting_id") for change in rows[0]["changes"]], [MEETING, MEETING])
        self.assertEqual(rows[0]["changes"][1]["change_reason"], "поставщик терминалов пересматривает цены")
        self.assertFalse(rows[0]["is_overdue"])
        overdue = [row["id"] for row in state["next_actions"]["overdue_commitments"]]
        self.assertNotIn(ESTIMATE, overdue, "перенесённый срок ещё не наступил — это не просрочка")
        # а наше обещание принять решение по оплате до 2026-11-10 к 15 ноября просрочено — обязательства нашей стороны тоже контролируются
        self.assertEqual(overdue, [PAYMENT_DECISION])
        center = (self.root / "system" / "control_center.md").read_text(encoding="utf-8")
        self.assertIn("Сроки, перенесённые повторно", center)
        self.assertIn("2026-11-05 → 2026-11-12 → 2026-11-19", center)
        self.assertEqual(self.queue_categories().get("commitment_rescheduled"), "open")


    def test_promise_raised_on_two_later_meetings_without_result(self) -> None:
        self.base_with_fixture_applied()
        original = read_json(FIXTURE / "analysis" / "07_commitments.json")["commitments"][0]
        for index, (meeting_id, day, quote) in enumerate((
            (SECOND, "2026-11-12", "Смету пришлём к девятнадцатому, сейчас ждём цены на терминалы."),
            ("2026-11-26-polaris-nordtech-status", "2026-11-26", "Смета будет на следующей неделе, поставщик ещё не ответил."),
        )):
            transcript = self.tmp / f"t{index}.txt"
            transcript.write_text(f"[00:01:00] Speaker 2: {quote}\n", encoding="utf-8")
            created = run(self.root, "source", "create", "--meeting", meeting_id, "--transcript", str(transcript), "--captured-at", f"{day}T15:00:00+03:00")
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            again = dict(original, meeting_id=meeting_id, source_quote=quote, source_timestamp="00:01:00")
            doc = {"meeting_id": meeting_id, "commitments": [again], "open_questions": []}
            (self.root / "negotiations" / meeting_id / "analysis" / "07_commitments.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            state = self.control_center("2026-12-01")
            if index == 0:
                self.assertEqual(state["stalled"]["unanswered_promises"], [], "одно напоминание — ещё не зависание")
        rows = state["stalled"]["unanswered_promises"]
        self.assertEqual([row["id"] for row in rows], [ESTIMATE])
        self.assertEqual(rows[0]["reminder_meetings"], 2)
        self.assertEqual(rows[0]["reminders"][1]["episodes"][0]["quote"], "Смета будет на следующей неделе, поставщик ещё не ответил.")
        self.assertIn("Обещания другой стороны без результата после напоминаний", (self.root / "system" / "control_center.md").read_text(encoding="utf-8"))
        self.assertEqual(self.queue_categories().get("unanswered_promise"), "open")


class WorkspaceSchemaTests(ControlAreaTestCase):
    def test_status_shows_schema_version_and_warns_when_migration_needed(self) -> None:
        self.register()
        status = payload(run(self.root, "status"))
        self.assertEqual(status["workspace"]["state"], "current")
        self.assertEqual(status["workspace"]["schema_version"], 2)
        document = read_json(self.root / WORKSPACE)
        self.assertEqual(document["origin"], "new_workspace")
        self.assertIsNotNone(document["created_at"])
        plugin = read_json(Path(NA).resolve().parents[1] / ".claude-plugin" / "plugin.json")
        self.assertEqual(document["plugin_versions"][0]["plugin_version"], plugin["version"])
        self.assertIn("Версия схемы базы 2", plain(self.root, "status").stdout)

        (self.root / WORKSPACE).unlink()  # папка, созданная до 0.6.0
        self.assertEqual(payload(run(self.root, "status"))["workspace"]["state"], "needs_migration")
        warned = plain(self.root, "status").stdout
        self.assertIn("ВНИМАНИЕ", warned)
        self.assertIn("na.py migrate", warned)

        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(payload(run(empty, "status"))["workspace"]["state"], "new")
        self.assertEqual(list(empty.iterdir()), [], "status ничего не создаёт")

    def test_migrate_plan_changes_nothing_and_confirm_is_idempotent(self) -> None:
        self.base_with_fixture_applied()
        (self.root / WORKSPACE).unlink()
        cards = ["knowledge/people/p-krylova-polaris.md", "knowledge/people/p-danilov-nordtech.md", "knowledge/organizations/org-nordtech.md"]
        before = snapshot(self.root)

        planned = run(self.root, "migrate")
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        plan = payload(planned)
        self.assertEqual(plan["status"], "plan")
        self.assertEqual([step["id"] for step in plan["plan"]["steps"]], ["0001-record-schema-version", "0002-name-variant-fields"])
        self.assertEqual(sorted(change["path"] for change in plan["plan"]["steps"][1]["changes"]), sorted(cards))
        self.assertEqual(snapshot(self.root), before, "без --confirm ничего не меняется")

        applied = run(self.root, "migrate", "--confirm")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(payload(applied)["status"], "applied")
        document = read_json(self.root / WORKSPACE)
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(document["origin"], "legacy_folder")
        self.assertIsNone(document["created_at"])
        self.assertEqual([item["id"] for item in document["migrations_applied"]], ["0001-record-schema-version", "0002-name-variant-fields"])
        for card in cards:
            old_lines = before[card].decode("utf-8").split("\n")
            new_lines = (self.root / card).read_text(encoding="utf-8").split("\n")
            closing = new_lines.index("---", 1)
            self.assertTrue(any(line.startswith("speech_variants: []") for line in new_lines[1:closing]), card)
            self.assertTrue(any(line.startswith("asr_variants: []") for line in new_lines[1:closing]), card)
            self.assertEqual([line for line in new_lines if not line.startswith(("speech_variants:", "asr_variants:"))], old_lines, f"{card}: содержание и строки (m) не тронуты")
        self.assertIn("(m) Предпочитает получать смету до внутреннего бюджетного комитета.", (self.root / cards[0]).read_text(encoding="utf-8"))
        after = snapshot(self.root)
        untouched = [path for path in before if path not in cards]
        self.assertEqual({path: after[path] for path in untouched}, {path: before[path] for path in untouched}, "реестры, журнал и встречи не тронуты")

        again = run(self.root, "migrate", "--confirm")
        self.assertEqual(again.returncode, 0)
        self.assertEqual(payload(again)["status"], "nothing_to_do")
        self.assertEqual(snapshot(self.root), after, "повторный migrate --confirm ничего не меняет")
        self.assertEqual(payload(run(self.root, "status"))["workspace"]["state"], "current")


class CounterpartyReviewTests(ControlAreaTestCase):
    def test_counterparty_review_splits_promises_by_confirmation(self) -> None:
        # обязательства обеих сторон — из фикстуры (07 и предложение базы)
        self.base_with_fixture_applied()
        # смета, схема зонирования и решение по оплате закрыты со слов пользователя
        self.apply_proposal(MEETING, proposal(MEETING, [
            {"registry": "knowledge/commitments/open.jsonl", "action": "close", "id": ESTIMATE, "record": {"status": "done", "follow_up": {"reminder_date": "2026-11-03", "reminder_sent": False, "actual_completion_date": "2026-11-04", "completion_notes": "пользователь сообщил, что смета получена"}}},
            {"registry": "knowledge/commitments/open.jsonl", "action": "close", "id": ZONING, "record": {"status": "done", "follow_up": {"reminder_date": None, "reminder_sent": False, "actual_completion_date": "2026-10-30", "completion_notes": "схему отправили"}}},
            {"registry": "knowledge/commitments/open.jsonl", "action": "close", "id": PAYMENT_DECISION, "record": {"status": "done", "follow_up": {"reminder_date": "2026-11-08", "reminder_sent": False, "actual_completion_date": "2026-11-09", "completion_notes": "решение по оплате сообщили Нордтеху"}}},
        ]))
        # условия гарантии закрыты по документу, зарегистрированному как источник
        document = self.tmp / "warranty-terms.txt"
        document.write_text("Условия гарантии на оборудование складского учёта. Стр. 1–2.\n", encoding="utf-8")
        doc_meeting = "2026-11-06-nordtech-warranty-terms-doc"
        created = run(self.root, "source", "create", "--meeting", doc_meeting, "--transcript", str(document), "--provider", "document", "--captured-at", "2026-11-06T10:00:00+03:00")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        self.apply_proposal(doc_meeting, proposal(doc_meeting, [
            {"registry": "knowledge/commitments/open.jsonl", "action": "close", "id": WARRANTY, "record": {
                "status": "done", "fulfillment_evidence": [{"source_id": doc_meeting, "evidence_class": "confirmed_fact", "quote": None, "page_or_cell": "стр. 1–2", "confidence": "high"}],
                "follow_up": {"reminder_date": None, "reminder_sent": False, "actual_completion_date": "2026-11-06", "completion_notes": None}}},
        ]))

        completed = run(self.root, "counterparty", "--org", "Нордтех")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        review = payload(completed)
        self.assertEqual(review["organization"]["id"], "org-nordtech")
        self.assertEqual([person["id"] for person in review["people"]], ["p-danilov-nordtech"])
        groups = {row["id"]: row["group"] for row in review["their_commitments"]}
        self.assertEqual(groups, {ESTIMATE: "user_words", WARRANTY: "document", SUPPORT_COST: "talk_only"})
        self.assertEqual(review["summary"]["their_by_group"], {"document": 1, "user_words": 1, "talk_only": 1, "cancelled": 0})
        # наши обещания им: срок «до пятницы» без даты — «в срок» не оценивается; решение по оплате — в срок
        ours = review["our_commitments"]
        self.assertEqual([(row["id"], row["group"], row["on_time"]) for row in ours], [(API_DOCS, "talk_only", None), (PAYMENT_DECISION, "user_words", True), (ZONING, "user_words", None)])
        self.assertEqual(review["summary"]["our_commitments"], 3)
        self.assertTrue(review["positions"], "позиции из карты аргументов встречи")
        self.assertEqual([risk["id"] for risk in review["risks"]], ["r-2026-10-28-api-documentation"])
        self.assertEqual(review["outputs"], ["system/counterparty/org-nordtech.json", "system/counterparty/org-nordtech.md"])
        text = (self.root / "system" / "counterparty" / "org-nordtech.md").read_text(encoding="utf-8")
        self.assertTrue(text.split("\n")[2].startswith("**Данные:**"))
        for heading in ("Подтверждено документом", "Подтверждено только словами пользователя", "Не подтверждено — есть только разговор", "## 2. Как мы выполняли обещания им", "## 3. Позиции и их изменения", "## 5. Пробелы данных"):
            self.assertIn(heading, text)

        # период: закрытое до начала периода не попадает, открытое — остаётся
        later = payload(run(self.root, "counterparty", "--org", "org-nordtech", "--since", "2026-12-01"))
        self.assertEqual([row["id"] for row in later["their_commitments"]], [SUPPORT_COST])
        self.assertEqual([row["id"] for row in later["our_commitments"]], [API_DOCS])
        self.assertEqual(run(self.root, "counterparty", "--org", "Несуществующая компания").returncode, 2)
        self.assertEqual(run(self.root, "counterparty", "--org", "Нордтех", "--since", "28.10.2026").returncode, 2)
