"""Полный учёт объектов разбора в предложении и решения пользователя по пунктам (knowledge decide)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from run_tests import ANALYSIS_FILES, FIXTURE, MEETING, SPEAKER_MAP, WorkspaceTestCase, payload, read_json, run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from na_engine.common import read_jsonl  # noqa: E402


KRYLOVA = "knowledge/people/p-krylova-polaris.md"
TOPIC = "knowledge/topics/wms-three-warehouses.md"
RISK_ID = "r-2026-10-28-api-documentation"
COMMITMENT_ID = "c-2026-10-28-nordtech-estimate"
AUTHORITY_LINE = "- Принимает окончательное решение по схеме оплаты проекта складского учёта (источник: [[2026-10-28-polaris-nordtech-wms-terms]] `00:07:47`)."
GUARANTEE_LINE = "- Условия гарантии на оборудование (перенесено на следующую встречу)."


def full_accounting() -> list[dict]:
    """Учёт всех обязательных объектов синтетической встречи: каждому — ровно одна судьба."""
    return [
        {"ref": "participant:p-krylova-polaris", "fate": "record", "target": {"file": KRYLOVA}, "certainty": "sure"},
        {"ref": "participant:p-danilov-nordtech", "fate": "record", "target": {"file": "knowledge/people/p-danilov-nordtech.md"}, "certainty": "sure"},
        {"ref": "participant:p-zaytseva-polaris", "fate": "skip", "reason": "Консультативная роль в одной встрече; новых фактов для карточки нет", "certainty": "sure"},
        {"ref": "organization:org-polaris", "fate": "skip", "reason": "Наша организация: отдельная карточка в базе не ведётся", "certainty": "sure"},
        {"ref": "organization:org-nordtech", "fate": "record", "target": {"file": "knowledge/organizations/org-nordtech.md"}, "certainty": "sure"},
        {"ref": f"commitment:{COMMITMENT_ID}", "fate": "record", "target": {"registry": "knowledge/commitments/open.jsonl", "id": COMMITMENT_ID}, "certainty": "sure"},
        {"ref": "question:1", "fate": "record", "target": {"file": TOPIC, "fragment": GUARANTEE_LINE}, "certainty": "sure"},
        {"ref": f"risk:{RISK_ID}", "fate": "record", "target": {"registry": "knowledge/risks/active.jsonl", "id": RISK_ID}, "certainty": "sure"},
        {"ref": "decision:1", "fate": "record", "target": {"registry": "knowledge/decisions/register.jsonl", "id": "d-2026-10-28-payment-scheme"}, "certainty": "sure"},
        {"ref": "authority:1", "fate": "record", "target": {"file": KRYLOVA, "fragment": AUTHORITY_LINE}, "certainty": "sure"},
        {"ref": "unresolved:1", "fate": "merge", "target": {"file": TOPIC, "fragment": GUARANTEE_LINE}, "reason": "Тот же вопрос, что question:1: в теме записан один раз", "certainty": "sure"},
    ]


class AccountingTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Встреча с разбором без шага validate: учёт и diff от него не зависят
        self.register()
        self.assertEqual(run(self.root, "source", "gate", "--meeting", MEETING, "--consent", "yes", "--masking", "none", "--sensitivity", "internal", "--routes", "local_only").returncode, 0)
        self.assertEqual(run(self.root, "speakers", "set", "--meeting", MEETING, "--map", SPEAKER_MAP).returncode, 0)
        for name in ANALYSIS_FILES:
            shutil.copyfile(FIXTURE / "analysis" / name, self.meeting / "analysis" / name)
        (self.root / "knowledge" / "people").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE / "existing_p-krylova-polaris.md", self.root / KRYLOVA)

    # --- помощники ------------------------------------------------------------

    def write_proposal(self, accounting: list[dict] | None, doubtful: tuple[str, ...] = ()) -> dict:
        proposal = read_json(FIXTURE / "knowledge_proposal.json")
        if accounting is not None:
            for entry in accounting:
                if entry["ref"] in doubtful:
                    entry["certainty"] = "doubtful"
                    entry.setdefault("question", "Подтвердите пункт.")
            proposal["accounting"] = accounting
        (self.meeting / "knowledge_proposal.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        return proposal

    def diff(self, expected_code: int = 0) -> dict:
        completed = run(self.root, "knowledge", "diff", "--meeting", MEETING)
        self.assertEqual(completed.returncode, expected_code, completed.stdout + completed.stderr)
        return payload(completed)

    def decide(self, item: str, decision: str, *extra: str, expected_code: int = 0) -> dict:
        completed = run(self.root, "knowledge", "decide", "--meeting", MEETING, "--item", item, "--decision", decision, *extra)
        self.assertEqual(completed.returncode, expected_code, completed.stdout + completed.stderr)
        return payload(completed)

    def apply(self) -> tuple[int, dict]:
        completed = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        return completed.returncode, payload(completed)

    def diff_text(self) -> str:
        return (self.meeting / "knowledge_diff.md").read_text(encoding="utf-8")

    def registry(self, name: str) -> list[dict]:
        return read_jsonl(self.root / "knowledge" / name)

    def assert_base_untouched(self) -> None:
        self.assertFalse((self.root / "knowledge" / "people" / "p-danilov-nordtech.md").exists())
        self.assertEqual(self.registry("commitments/open.jsonl"), [])
        self.assertEqual(self.registry("risks/active.jsonl"), [])
        self.assertFalse((self.meeting / "knowledge_apply.json").exists())

    # --- полнота учёта ----------------------------------------------------------

    def test_full_accounting_shows_section_and_applies(self) -> None:
        self.write_proposal(full_accounting())
        result = self.diff()
        self.assertEqual(result["accounting"]["objects"], 11)
        self.assertEqual(result["accounting"]["items"], 11)
        self.assertEqual(result["pending_decisions"], [])
        text = self.diff_text()
        for needle in ("## Учёт объектов разбора", "### Уверенные — применяются общим подтверждением", "**Записываем**", "**Объединяем**", "**Не переносим**", "почему: Наша организация", "Сомнительных пунктов нет."):
            self.assertIn(needle, text)
        self.assertNotIn("Учёт не заполнен", text)
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)
        self.assertEqual(applied["accounting"]["items"], 11)
        self.assertEqual([item["id"] for item in self.registry("risks/active.jsonl")], [RISK_ID])
        history = read_json(self.meeting / "knowledge_apply.json")["history"][-1]
        self.assertTrue(history["accounting"]["present"])

    def test_unaccounted_object_blocks_diff_and_apply(self) -> None:
        accounting = [entry for entry in full_accounting() if entry["ref"] != f"risk:{RISK_ID}"]
        self.write_proposal(accounting)
        result = self.diff(expected_code=1)
        self.assertFalse(result["valid"])
        self.assertTrue(any(f"не учтён объект разбора risk:{RISK_ID}" in error for error in result["errors"]))
        self.assertIn("## Ошибки предусловий (применение заблокировано)", self.diff_text())
        self.decide("decision:1", "accept", expected_code=2)  # вопросы — только по предложению без ошибок
        self.assertFalse((self.meeting / "knowledge_decisions.jsonl").exists())
        code, _ = self.apply()
        self.assertNotEqual(code, 0)
        self.assert_base_untouched()

    def test_double_accounting_blocks_diff(self) -> None:
        accounting = full_accounting()
        accounting.append({"ref": f"commitment:{COMMITMENT_ID}", "fate": "skip", "reason": "повтор", "certainty": "sure"})
        self.write_proposal(accounting)
        result = self.diff(expected_code=1)
        self.assertTrue(any("двойной учёт" in error and COMMITMENT_ID in error for error in result["errors"]))
        code, _ = self.apply()
        self.assertNotEqual(code, 0)
        self.assert_base_untouched()

    def test_skip_without_reason_blocks_diff(self) -> None:
        accounting = full_accounting()
        for entry in accounting:
            if entry["ref"] == "participant:p-zaytseva-polaris":
                del entry["reason"]
        self.write_proposal(accounting)
        result = self.diff(expected_code=1)
        self.assertTrue(any("participant:p-zaytseva-polaris" in error and "reason" in error for error in result["errors"]))

    def test_record_target_must_be_in_proposal(self) -> None:
        accounting = full_accounting()
        for entry in accounting:
            if entry["ref"] == "organization:org-polaris":
                entry.update({"fate": "record", "target": {"file": "knowledge/organizations/org-polaris.md"}})
        self.write_proposal(accounting)
        result = self.diff(expected_code=1)
        self.assertTrue(any("organization:org-polaris" in error and "нет в files предложения" in error for error in result["errors"]))

    # --- сомнительные пункты и решения ---------------------------------------------

    def test_doubtful_item_without_decision_refuses_apply(self) -> None:
        self.write_proposal(full_accounting(), doubtful=(f"risk:{RISK_ID}",))
        result = self.diff()
        self.assertEqual(result["pending_decisions"], [f"risk:{RISK_ID}"])
        text = self.diff_text()
        sure_part, doubtful_part = text.split("### Сомнительные — нужен ответ по каждому пункту", 1)
        self.assertIn(f"commitment:{COMMITMENT_ID}", sure_part)
        self.assertIn(f"risk:{RISK_ID}", doubtful_part)
        self.assertIn("### Вопросы пользователю — одним блоком", text)
        self.assertIn("Ответ: принять · отклонить · переписать", text)
        completed = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("нет решения пользователя по сомнительным пунктам", payload(completed)["error"])
        self.assert_base_untouched()

    def test_reject_keeps_item_out_of_base(self) -> None:
        self.write_proposal(full_accounting(), doubtful=(f"risk:{RISK_ID}",))
        self.diff()
        decided = self.decide(f"risk:{RISK_ID}", "reject")
        self.assertEqual(decided["decision"]["source"], "user_confirmation")
        self.assertEqual(decided["pending"], [])
        self.assertTrue((self.meeting / "knowledge_decisions.jsonl").is_file())
        result = self.diff()
        self.assertNotIn(f"| knowledge/risks/active.jsonl | append | {RISK_ID} |", self.diff_text())
        self.assertIn("Решение пользователя", self.diff_text())
        self.assertTrue(any(RISK_ID in warning and "упоминается" in warning for warning in result["warnings"]))
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)
        self.assertEqual(self.registry("risks/active.jsonl"), [])
        self.assertEqual([item["id"] for item in self.registry("commitments/open.jsonl")], [COMMITMENT_ID])
        decisions = read_json(self.meeting / "knowledge_apply.json")["history"][-1]["accounting"]["decisions"]
        self.assertEqual([(item["item"], item["decision"]) for item in decisions], [(f"risk:{RISK_ID}", "reject")])

    def test_rewrite_records_new_wording(self) -> None:
        self.write_proposal(full_accounting(), doubtful=(f"commitment:{COMMITMENT_ID}", "authority:1"))
        self.diff()
        new_what = "прислать уточнённую смету по трём складам с лицензиями на 24 пользователя"
        new_line = "- Окончательное решение по схеме оплаты принимает она (`00:07:47`)."
        self.decide(f"commitment:{COMMITMENT_ID}", "rewrite", "--text", new_what)
        self.decide("2", "rewrite", "--text", new_line)  # № сомнительного пункта из diff
        self.diff()
        self.assertIn(new_what, self.diff_text())
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)
        commitment = self.registry("commitments/open.jsonl")[0]
        self.assertEqual(commitment["what"], new_what)
        self.assertTrue(commitment["source_quote"].startswith("Я пришлю уточнённую смету"))  # цитата транскрипта не меняется
        card = (self.root / KRYLOVA).read_text(encoding="utf-8")
        self.assertIn(new_line, card)
        self.assertNotIn(AUTHORITY_LINE, card)
        self.assertIn("(m) Предпочитает получать смету до внутреннего бюджетного комитета.", card)

    def test_merge_joins_record_with_target(self) -> None:
        existing = {
            "id": "r-2026-09-api-delay", "type": "technology",
            "description": "Интеграция с учётной системой задерживается без документации по API.",
            "first_seen_meeting_id": "2026-09-15-polaris-nordtech-kickoff", "last_seen_meeting_id": "2026-09-15-polaris-nordtech-kickoff",
            "involved_companies": ["org-polaris"], "involved_people": ["p-krylova-polaris"], "linked_topic": "wms-api-integration",
            "likelihood": "medium", "impact_rub": None, "impact_qualitative": "high", "severity": "high",
            "mitigation": {"planned_actions": "Найти документацию у прежнего подрядчика.", "owner": "p-krylova-polaris", "deadline": None},
            "status": "open", "evidence": [{"quote": "Документации по API нет.", "meeting_id": "2026-09-15-polaris-nordtech-kickoff", "timestamp": "00:10:00"}],
            "confidence": "probable",
        }
        risks = self.root / "knowledge" / "risks" / "active.jsonl"
        risks.parent.mkdir(parents=True, exist_ok=True)
        risks.write_text(json.dumps(existing, ensure_ascii=False) + "\n", encoding="utf-8")
        self.write_proposal(full_accounting(), doubtful=(f"risk:{RISK_ID}",))
        self.diff()
        self.decide(f"risk:{RISK_ID}", "merge", "--target", "r-2026-09-api-delay")
        self.diff()
        self.assertIn("| knowledge/risks/active.jsonl | update | r-2026-09-api-delay |", self.diff_text())
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)
        active = self.registry("risks/active.jsonl")
        self.assertEqual([item["id"] for item in active], ["r-2026-09-api-delay"])
        merged = active[0]
        self.assertEqual(merged["description"], existing["description"])
        self.assertEqual(merged["status"], "open")
        self.assertEqual(len(merged["evidence"]), 2)
        self.assertEqual(merged["involved_companies"], ["org-polaris", "org-nordtech"])
        self.assertEqual(merged["last_seen_meeting_id"], MEETING)
        self.assertEqual(merged["merged_from"][0]["id"], RISK_ID)
        self.assertEqual(merged["merged_from"][0]["decided_by"], "user_confirmation")

    def test_merge_moves_card_fragment_into_target_section(self) -> None:
        self.write_proposal(full_accounting(), doubtful=("authority:1",))
        self.diff()
        self.decide("authority:1", "merge", "--target", f"{TOPIC}#Решения и варианты")
        self.diff()
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)
        card = (self.root / KRYLOVA).read_text(encoding="utf-8")
        topic = (self.root / TOPIC).read_text(encoding="utf-8")
        self.assertNotIn(AUTHORITY_LINE, card)
        self.assertIn("(m) Предпочитает получать смету", card)
        section = topic[topic.index("## Решения и варианты"):topic.index("## Поручения и обязательства")]
        self.assertIn(AUTHORITY_LINE, section)

    def test_decision_after_diff_requires_new_diff(self) -> None:
        self.write_proposal(full_accounting(), doubtful=(f"risk:{RISK_ID}",))
        self.diff()
        self.decide(f"risk:{RISK_ID}", "accept")
        completed = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("изменились решения по пунктам", payload(completed)["error"])
        self.assert_base_untouched()
        self.assertIsNone(read_json(self.meeting / "knowledge_diff_snapshot.json")["decisions_sha256"])
        self.diff()
        self.assertIsNotNone(read_json(self.meeting / "knowledge_diff_snapshot.json")["decisions_sha256"])
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)
        self.assertEqual([item["id"] for item in self.registry("risks/active.jsonl")], [RISK_ID])
        # повторный apply идемпотентен; решения после применения не принимаются
        again = run(self.root, "knowledge", "apply", "--meeting", MEETING, "--confirm")
        self.assertEqual(payload(again)["status"], "already_applied")
        self.decide(f"risk:{RISK_ID}", "reject", expected_code=2)

    def test_new_proposal_version_needs_new_answers(self) -> None:
        proposal = self.write_proposal(full_accounting(), doubtful=(f"risk:{RISK_ID}",))
        self.diff()
        self.decide(f"risk:{RISK_ID}", "accept")
        proposal["generated_at"] = "2026-10-28T17:00:00+03:00"  # пересобранное предложение — другой хеш
        (self.meeting / "knowledge_proposal.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.diff()
        self.assertEqual(result["pending_decisions"], [f"risk:{RISK_ID}"])
        self.assertTrue(any("прежним версиям предложения" in warning for warning in result["warnings"]))
        self.assertEqual(self.apply()[0], 2)

    def test_decide_guards(self) -> None:
        self.write_proposal(full_accounting(), doubtful=("participant:p-zaytseva-polaris", "participant:p-krylova-polaris"))
        self.diff()
        # непереносимый объект: переписать нельзя; отклонить — значит пересобрать предложение
        self.decide("participant:p-zaytseva-polaris", "rewrite", "--text", "x", expected_code=2)
        self.decide("participant:p-zaytseva-polaris", "reject")
        # отклонить карточку целиком нельзя, пока в ней остаётся другой пункт (authority:1)
        self.decide("participant:p-krylova-polaris", "reject", expected_code=2)
        self.decide("participant:p-krylova-polaris", "accept")
        self.decide("unknown:1", "accept", expected_code=2)
        self.decide("7", "accept", expected_code=2)
        listed = payload(run(self.root, "knowledge", "decide", "--meeting", MEETING, "--list"))
        self.assertEqual([item["item"] for item in listed["doubtful"]], ["participant:p-krylova-polaris", "participant:p-zaytseva-polaris"])
        result = self.diff(expected_code=1)
        self.assertTrue(any("пересоберите предложение" in error for error in result["errors"]))
        self.assertNotEqual(self.apply()[0], 0)
        self.assert_base_untouched()

    # --- профиль разбора --------------------------------------------------------------

    def test_light_profile_requires_only_01_07_08(self) -> None:
        set_profile = run(self.root, "profile", "set", "--meeting", MEETING, "--profile", "light")
        self.assertEqual(set_profile.returncode, 0, set_profile.stdout + set_profile.stderr)
        for name in ANALYSIS_FILES:
            if name not in ("01_extraction.json", "07_commitments.json", "08_risks.json"):
                (self.meeting / "analysis" / name).unlink()
        light_refs = ("decision:1", "authority:1", "unresolved:1")
        accounting = [entry for entry in full_accounting() if entry["ref"] not in light_refs]
        self.write_proposal(accounting)
        result = self.diff()
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["accounting"]["profile"], "light")
        self.assertEqual(result["accounting"]["objects"], 8)
        self.assertFalse(any("09_decision_authority" in warning for warning in result["warnings"]))
        self.assertIn("Профиль разбора: `light`", self.diff_text())
        # объект из артефакта вне профиля не требуется, а ссылка на него — ошибка: его в разборе нет
        accounting.append({"ref": "decision:1", "fate": "skip", "reason": "карта решений не строилась", "certainty": "sure"})
        self.write_proposal(accounting)
        self.assertTrue(any("decision:1" in error for error in self.diff(expected_code=1)["errors"]))
        # без 07 в light — по-прежнему предупреждение: это обязательный артефакт профиля
        accounting.pop()
        self.write_proposal(accounting)
        (self.meeting / "analysis" / "08_risks.json").unlink()
        warnings = self.diff(expected_code=1)["warnings"]
        self.assertTrue(any("08_risks.json" in warning for warning in warnings))

    def test_light_profile_with_extra_artifact_keeps_it_optional(self) -> None:
        self.assertEqual(run(self.root, "profile", "set", "--meeting", MEETING, "--profile", "light").returncode, 0)
        # 09 построен сверх профиля: его объекты можно учесть, но не обязательно
        accounting = [entry for entry in full_accounting() if entry["ref"] not in ("decision:1", "authority:1", "unresolved:1")]
        accounting.append({"ref": "decision:1", "fate": "record", "target": {"registry": "knowledge/decisions/register.jsonl", "id": "d-2026-10-28-payment-scheme"}, "certainty": "sure"})
        self.write_proposal(accounting)
        result = self.diff()
        self.assertEqual(result["accounting"]["objects"], 8)
        self.assertEqual(result["accounting"]["unaccounted"], [])
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)

    # --- совместимость ------------------------------------------------------------

    def test_legacy_proposal_without_accounting_warns_and_applies(self) -> None:
        self.write_proposal(None)
        result = self.diff()
        self.assertTrue(result["valid"])
        self.assertTrue(any("Учёт не заполнен" in warning for warning in result["warnings"]))
        self.assertFalse(result["accounting"]["present"])
        text = self.diff_text()
        self.assertIn("## Предупреждения", text)
        self.assertIn("Раздел `accounting` в предложении не заполнен", text)
        self.decide(f"risk:{RISK_ID}", "reject", expected_code=2)
        code, applied = self.apply()
        self.assertEqual(code, 0, applied)
        self.assertEqual(len(applied["files_written"]), 4)
        self.assertEqual([item["id"] for item in self.registry("risks/active.jsonl")], [RISK_ID])
        self.assertFalse(read_json(self.meeting / "knowledge_apply.json")["history"][-1]["accounting"]["present"])
