"""Регрессии защиты на реальных формах вызовов и журнала Codex, без запуска команд."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hooks"))
import guard_writes


class CodexHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Переговоры"
        self.root.mkdir()
        (self.root / ".na-workspace").touch()
        self.transcript = Path(self.tmp.name) / "rollout.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def verdict(self, name, data, entries=None, **extra):
        event = {"hook_event_name": "PreToolUse", "tool_name": name,
                 "tool_input": data, "cwd": str(self.root), **extra}
        if entries is not None:
            self.transcript.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
            event["transcript_path"] = str(self.transcript)
        result = guard_writes.decide(event)
        return result["hookSpecificOutput"]["permissionDecision"] if result else None

    @staticmethod
    def human(text):
        return {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": text}]}}

    @staticmethod
    def call(command):
        return {"type": "response_item", "payload": {"type": "function_call", "name": "functions.exec_command",
                "call_id": "show-1", "arguments": json.dumps({"cmd": command})}}

    def test_exec_cmd_and_namespaced_command_protect_database(self):
        for name in ("exec_command", "functions.exec_command"):
            for data in ({"cmd": "printf x > knowledge/people/test.md"},
                         {"cmd": r"Set-Content -LiteralPath knowledge\people\test.md -Value x", "shell": "pwsh"}):
                with self.subTest(name=name, data=data):
                    self.assertEqual(self.verdict(name, data), "deny")

    def test_command_workdir_is_used(self):
        self.assertEqual(self.verdict("exec_command", {
            "cmd": "printf x > knowledge/people/test.md", "workdir": str(self.root),
        }, cwd=str(Path(self.tmp.name))), "deny")

    def test_patch_raw_and_wrapped_forms_protect_sources_and_database(self):
        patch = "*** Begin Patch\n*** Update File: negotiations/test/transcript.txt\n@@\n-old\n+new\n*** End Patch"
        for name in ("apply_patch", "functions.apply_patch"):
            for data in (patch, {"command": patch}, {"input": patch}):
                with self.subTest(name=name, data=data):
                    self.assertEqual(self.verdict(name, data), "deny")
        move = "*** Begin Patch\n*** Update File: report.md\n*** Move to: knowledge/people/test.md\n@@\n-old\n+new\n*** End Patch"
        self.assertEqual(self.verdict("apply_patch", move), "deny")

    def test_unprotected_output_and_unrelated_workspace_are_allowed(self):
        self.assertIsNone(self.verdict("exec_command", {"cmd": "printf x > report.md"}))
        self.assertIsNone(self.verdict("exec_command", {
            "cmd": "printf x > knowledge/people/test.md", "workdir": str(Path(self.tmp.name)),
        }))

    def test_apply_needs_human_reply_after_codex_show(self):
        command = "python scripts/na.py knowledge apply --meeting test --confirm"
        shown = self.call("python scripts/na.py knowledge diff --meeting test")
        self.assertEqual(self.verdict("exec_command", {"cmd": command}, [self.human("Да, применяй"), shown]), "deny")
        self.assertIsNone(self.verdict("exec_command", {"cmd": command}, [shown, self.human("Да, применяй")]))
        self.assertEqual(self.verdict("exec_command", {"cmd": command}, [shown, self.human("Нет, сначала исправь")]), "deny")

    def test_no_consent_and_combined_show_apply_are_denied(self):
        command = "python scripts/na.py knowledge apply --meeting test --confirm"
        self.assertEqual(self.verdict("exec_command", {"cmd": command}, [self.human("Покажи изменения")]), "deny")
        combined = "python scripts/na.py knowledge diff --meeting test; " + command
        self.assertEqual(self.verdict("exec_command", {"cmd": combined}, [self.human("Применяй")]), "deny")

    def test_delivery_consent_has_same_ordering(self):
        show = self.call("python scripts/na.py deliver preview --file report.md --channel telegram")
        send = {"cmd": "python scripts/na.py deliver send --file report.md --confirm"}
        self.assertEqual(self.verdict("exec_command", send, [self.human("Отправляй"), show]), "deny")
        self.assertIsNone(self.verdict("exec_command", send, [show, self.human("Отправляй")]))

    def test_subagent_and_missing_transcript_preserve_policy(self):
        command = {"cmd": "python scripts/na.py knowledge apply --meeting test --confirm"}
        self.assertEqual(self.verdict("exec_command", command, [self.human("Применяй")], agent_id="helper"), "deny")
        self.assertIsNone(self.verdict("exec_command", command))


if __name__ == "__main__":
    unittest.main()
