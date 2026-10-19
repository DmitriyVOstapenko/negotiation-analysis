"""Хуки плагина (hooks/guard_writes.py, hooks/guard_decisions.py) и синтаксис компонентов: agents/*.md, hooks/hooks.json, описания навыков."""

from __future__ import annotations

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from run_tests import MEETING, WorkspaceTestCase

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "guard_writes.py"
sys.path.insert(0, str(REPO / "scripts"))
from na_engine.common import yaml_load  # noqa: E402

# Поля frontmatter, которые Claude Code применяет к агентам плагина (docs: plugins/components#agents).
PLUGIN_AGENT_FIELDS = {
    "name", "description", "model", "effort", "maxTurns", "tools", "disallowedTools", "skills", "memory",
    "background", "omitClaudeMd", "isolation", "color", "experimental",
}
KNOWN_TOOLS = {"Read", "Write", "Edit", "Grep", "Glob", "Bash", "PowerShell", "NotebookEdit", "WebFetch", "WebSearch", "LSP", "Skill", "TodoWrite", "ToolSearch"}


def guard(event: object | None = None, raw: str | None = None) -> tuple[int, dict | None, str]:
    data = raw if raw is not None else json.dumps(event, ensure_ascii=False)
    completed = subprocess.run([sys.executable, str(HOOK)], input=data.encode("utf-8"), capture_output=True, check=False, timeout=30)
    out = completed.stdout.decode("utf-8").strip()
    return completed.returncode, (json.loads(out) if out else None), completed.stderr.decode("utf-8", errors="replace")


def event(tool: str, path: str, cwd: str | None = None, key: str = "file_path") -> dict:
    payload = {"session_id": "t", "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {key: path, "content": "x"}, "tool_use_id": "toolu_test"}
    if cwd:
        payload["cwd"] = cwd
    return payload


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        raise AssertionError(f"{path}: нет frontmatter между строками ---")
    # Строгий YAML (как у Claude Code) не допускает «: » и « #» внутри простого скаляра: такой
    # frontmatter не разбирается, и агент грузится с пропуском всех полей, включая tools.
    for line in match.group(1).splitlines():
        top = re.match(r"^([A-Za-z][\w-]*):\s+(.*)$", line)
        if top and top.group(2) and top.group(2)[0] not in "|>\"'[{":
            value = top.group(2)
            if ": " in value or " #" in value:
                raise AssertionError(f"{path}: поле {top.group(1)} — простой скаляр с «: » или « #»; используйте блок > или кавычки")
    try:
        import yaml  # type: ignore

        strict = yaml.safe_load(match.group(1))
        if not isinstance(strict, dict):
            raise AssertionError(f"{path}: frontmatter — не словарь")
    except ImportError:
        pass
    data = yaml_load(match.group(1))
    if not isinstance(data, dict):
        raise AssertionError(f"{path}: frontmatter — не словарь")
    return data, match.group(2)


class GuardWritesTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.register()

    def decision(self, result: tuple[int, dict | None, str]) -> str | None:
        code, output, stderr = result
        self.assertEqual(code, 0, stderr)
        if output is None:
            return None
        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        self.assertTrue(specific["permissionDecisionReason"])
        return specific["permissionDecision"]

    def test_direct_write_to_knowledge_is_denied(self) -> None:
        code, output, _ = guard(event("Write", str(self.root / "knowledge" / "people" / "ivan-petrov.md")))
        self.assertEqual(code, 0)
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("knowledge-update", reason)
        self.assertIn("apply --confirm", reason)
        for tool in ("Edit", "MultiEdit"):
            self.assertEqual(self.decision(guard(event(tool, str(self.root / "knowledge" / "commitments" / "open.jsonl")))), "deny")
        self.assertEqual(self.decision(guard(event("NotebookEdit", str(self.root / "knowledge" / "x.ipynb"), key="notebook_path"))), "deny")

    def test_meeting_report_and_analysis_are_allowed(self) -> None:
        for name in ("report.md", "analysis/07_commitments.json", "analysis/parts/07_commitments.part-1.json", "knowledge_proposal.json", "review.yaml"):
            self.assertIsNone(self.decision(guard(event("Write", str(self.meeting / name)))), name)

    def test_transcript_and_provider_note_are_immutable(self) -> None:
        self.assertEqual(self.decision(guard(event("Edit", str(self.meeting / "transcript.txt")))), "deny")
        self.assertEqual(self.decision(guard(event("Write", str(self.meeting / "note.md")))), "deny")
        _, output, _ = guard(event("Edit", str(self.meeting / "transcript.txt")))
        self.assertIn("review.yaml", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_outside_workspace_is_allowed(self) -> None:
        other = self.tmp / "other-project"
        (other / "knowledge" / "people").mkdir(parents=True)
        self.assertIsNone(self.decision(guard(event("Write", str(other / "knowledge" / "people" / "notes.md")))))
        self.assertIsNone(self.decision(guard(event("Write", str(other / "negotiations" / "x" / "transcript.txt")))))
        self.assertIsNone(self.decision(guard(event("Edit", str(REPO / "skills" / "meeting-analysis" / "SKILL.md")))))
        self.assertIsNone(self.decision(guard(event("Write", str(self.tmp / "loose.md")))))

    def test_broken_or_foreign_input_passes(self) -> None:
        for raw in ("", "not json", "[]", "null", '{"tool_name": 5}', '{"tool_name": "Write"}', '{"tool_name": "Write", "tool_input": {"file_path": 7}}'):
            code, output, _ = guard(raw=raw)
            self.assertEqual(code, 0, raw)
            self.assertIsNone(output, raw)
        self.assertIsNone(self.decision(guard(event("Read", str(self.root / "knowledge" / "index.md")))))
        foreign = event("Write", str(self.root / "knowledge" / "index.md"))
        foreign["hook_event_name"] = "PostToolUse"
        self.assertIsNone(self.decision(guard(foreign)))

    def test_relative_and_dotted_paths_resolve(self) -> None:
        self.assertEqual(self.decision(guard(event("Write", "knowledge/people/x.md", cwd=str(self.root)))), "deny")
        dotted = str(self.meeting / ".." / ".." / "knowledge" / "people" / "x.md")
        self.assertEqual(self.decision(guard(event("Write", dotted))), "deny")

    def test_strategy_cards_ask_the_user(self) -> None:
        path = self.root / "knowledge" / "strategies" / "negociar-ru" / "index.jsonl"
        self.assertEqual(self.decision(guard(event("Write", str(path)))), "ask")

    def test_workspace_markers(self) -> None:
        for marker in (".na-workspace", "knowledge/workspace.json", "system/workspace.json", "knowledge/events/ledger.jsonl"):
            base = Path(tempfile.mkdtemp(prefix="na-guard-", dir=self.tmp))
            (base / marker).parent.mkdir(parents=True, exist_ok=True)
            (base / marker).write_text("", encoding="utf-8")
            self.assertEqual(self.decision(guard(event("Write", str(base / "knowledge" / "topics" / "t.md")))), "deny", marker)

    def test_project_working_copy_allows_loading_only(self) -> None:
        copy = self.tmp / "session" / "na-workspace"
        (copy / "knowledge" / "people").mkdir(parents=True)
        (copy / "negotiations").mkdir()
        existing = copy / "knowledge" / "people" / "loaded.md"
        existing.write_text("loaded", encoding="utf-8")
        self.assertIsNone(self.decision(guard(event("Write", str(copy / "knowledge" / "people" / "new.md")))))
        self.assertEqual(self.decision(guard(event("Write", str(existing)))), "deny")
        self.assertEqual(self.decision(guard(event("Edit", str(existing)))), "deny")

    @unittest.skipIf(os.name == "nt", "символические ссылки на Windows требуют прав")
    def test_symlinked_path_is_resolved(self) -> None:
        link = self.tmp / "shortcut"
        os.symlink(self.root / "knowledge", link)
        self.assertEqual(self.decision(guard(event("Write", str(link / "people" / "x.md")))), "deny")


def shell(command: str, cwd: Path, transcript: Path | None = None, tool: str = "Bash", **extra: object) -> dict:
    payload = {"session_id": "t", "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command}, "cwd": str(cwd), "tool_use_id": "toolu_test"}
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    payload.update(extra)
    return payload


class GuardShellAndPatchTests(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.register()

    def verdict(self, payload: dict) -> str | None:
        code, output, stderr = guard(payload)
        self.assertEqual(code, 0, stderr)
        return output["hookSpecificOutput"]["permissionDecision"] if output else None

    def test_shell_writes_into_knowledge_are_denied(self) -> None:
        commands = [
            "cat > knowledge/people/x.md <<'EOF'\nКарточка: Иванов — ЛПР, «он сказал 'да'»\nEOF",
            "echo '{}' >> knowledge/commitments/open.jsonl",
            "sed -i 's/open/done/' knowledge/commitments/open.jsonl",
            "python3 -c \"open('knowledge/risks/active.jsonl','a').write('{}')\"",
            "python3 - <<'PY'\nfrom pathlib import Path\nPath('knowledge/topics/t.md').write_text('x')\nPY",
            "rm knowledge/people/old.md",
            "mv knowledge/people/a.md /tmp/a.md",
            "cp /tmp/card.md knowledge/people/",
            "cd knowledge && echo x | tee people/y.md",
            f"ROOT={self.root}; printf x > $ROOT/knowledge/index.md",
            "python3 /opt/plugin/scripts/na.py --json status > knowledge/index.md",
        ]
        for command in commands:
            self.assertEqual(self.verdict(shell(command, self.root)), "deny", command)

    def test_shell_reads_and_engine_calls_pass(self) -> None:
        commands = [
            "cat knowledge/people/*.md",
            "grep -rn Polaris knowledge/ > /tmp/hits.txt 2>&1",
            "python3 -c \"import json; print(json.load(open('knowledge/workspace.json')))\"",
            f"python3 /opt/plugin/scripts/na.py --root {self.root} knowledge diff --meeting {MEETING}",
            f"echo x > negotiations/{MEETING}/analysis/parts/notes.txt",
            "ls -la && git status",
        ]
        for command in commands:
            self.assertIsNone(self.verdict(shell(command, self.root)), command)

    def test_shell_source_and_workspace_removal_are_denied(self) -> None:
        for command in (f"rm negotiations/{MEETING}/transcript.txt", f"rm -rf negotiations/{MEETING}", f"rm -rf {self.root}", f"truncate -s 0 negotiations/{MEETING}/transcript.txt"):
            self.assertEqual(self.verdict(shell(command, self.root)), "deny", command)
        self.assertIsNone(self.verdict(shell(f"rm negotiations/{MEETING}/report_auto.md", self.root)))

    def test_shell_outside_workspace_and_powershell(self) -> None:
        other = self.tmp / "elsewhere"
        (other / "knowledge").mkdir(parents=True)
        self.assertIsNone(self.verdict(shell("echo x > knowledge/a.md && rm -rf knowledge", other)))
        self.assertEqual(self.verdict(shell("Set-Content -Path knowledge\\people\\x.md -Value 'x'", self.root, tool="PowerShell")), "deny")
        self.assertEqual(self.verdict(shell("Remove-Item -Force knowledge/people/x.md", self.root, tool="PowerShell")), "deny")

    def test_apply_patch_paths(self) -> None:
        def patch(body: str) -> dict:
            return {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": f"*** Begin Patch\n{body}\n*** End Patch"}, "cwd": str(self.root)}

        self.assertEqual(self.verdict(patch("*** Add File: knowledge/people/new.md\n+# Новый")), "deny")
        self.assertEqual(self.verdict(patch(f"*** Update File: negotiations/{MEETING}/transcript.txt\n@@\n-a\n+b")), "deny")
        self.assertIsNone(self.verdict(patch(f"*** Update File: negotiations/{MEETING}/report.md\n@@\n-a\n+b")))
        self.assertEqual(self.verdict(patch(f"*** Update File: negotiations/{MEETING}/report.md\n*** Move to: knowledge/topics/r.md")), "deny")


class OwnerDecisionTests(WorkspaceTestCase):
    NA = "python3 /opt/plugin/scripts/na.py"

    def setUp(self) -> None:
        super().setUp()
        self.register()
        self.transcript = self.tmp / "session.jsonl"
        self.entries: list[dict] = []

    def human(self, text: str) -> None:
        self.entries.append({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "<system-reminder>служебное</system-reminder>"}, {"type": "text", "text": text}]}})

    def harness(self, text: str) -> None:
        self.entries.append({"type": "user", "message": {"role": "user", "content": text}})

    def ran(self, command: str, tool_id: str = "toolu_run") -> None:
        self.entries.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}]}})
        self.entries.append({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}})

    def asked(self, answer: str | None, question: str = "Применить изменения базы?") -> None:
        self.entries.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_ask", "name": "AskUserQuestion", "input": {"questions": [{"question": question, "header": "База", "options": [{"label": "Применить"}, {"label": "Не применять"}], "multiSelect": False}]}}]}})
        if answer is not None:
            self.entries.append({
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_ask", "content": f'User has answered your questions: "{question}"="{answer}". You can now continue.'}]},
                "toolUseResult": {"questions": [], "answers": {question: answer}},
            })

    def verdict(self, command: str, **extra: object) -> str | None:
        self.transcript.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in self.entries) + "\n", encoding="utf-8")
        code, output, stderr = guard(shell(command, self.root, self.transcript, **extra))
        self.assertEqual(code, 0, stderr)
        return output["hookSpecificOutput"]["permissionDecision"] if output else None

    def apply(self) -> str:
        return f"{self.NA} --root {self.root} knowledge apply --meeting {MEETING} --confirm"

    def test_apply_after_explicit_consent(self) -> None:
        self.ran(f"{self.NA} --root {self.root} knowledge diff --meeting {MEETING}")
        self.human("Да, применяй")
        self.harness("<task-notification>фоновая задача завершена</task-notification>")
        self.assertIsNone(self.verdict(self.apply()))

    def test_apply_without_consent_is_denied(self) -> None:
        self.human("Покажи diff")
        self.assertEqual(self.verdict(self.apply()), "deny")
        self.entries.clear()
        self.human("Нет, подожди, сначала поправь риски")
        self.assertEqual(self.verdict(self.apply()), "deny")
        self.entries.clear()
        self.human("не применяй пока")
        self.assertEqual(self.verdict(self.apply()), "deny")

    def test_windows_path_without_quotes_is_still_checked(self) -> None:
        self.human("Покажи diff")
        self.assertEqual(self.verdict(f"py -3 C:\\p\\scripts\\na.py --root C:\\w knowledge apply --meeting {MEETING} --confirm", tool="PowerShell"), "deny")
        self.assertEqual(self.verdict(f"py -3 C:\\p\\scripts\\na.py --root C:\\w knowledge apply --meeting {MEETING} --confirm"), "deny")

    def test_no_problem_is_consent(self) -> None:
        self.ran(f"{self.NA} --root {self.root} knowledge diff --meeting {MEETING}")
        self.human("Нет проблем, применяй")
        self.assertIsNone(self.verdict(self.apply()))

    def test_apply_needs_answer_after_the_diff(self) -> None:
        self.human("Да, давай")
        self.ran(f"{self.NA} --root {self.root} knowledge diff --meeting {MEETING}")
        self.assertEqual(self.verdict(self.apply()), "deny")
        both = f"{self.NA} knowledge diff --meeting {MEETING} && {self.apply()}"
        self.entries.clear()
        self.human("применяй")
        self.assertEqual(self.verdict(both), "deny")

    def test_ask_user_question_answers(self) -> None:
        self.human("Обнови базу по встрече")
        self.ran(f"{self.NA} knowledge diff --meeting {MEETING}")
        self.asked("Применить")
        self.assertIsNone(self.verdict(self.apply()))
        self.entries.clear()
        self.human("Обнови базу по встрече")
        self.asked("Не применять")
        self.assertEqual(self.verdict(self.apply()), "deny")
        self.entries.clear()
        self.human("Обнови базу по встрече")
        self.asked(None)  # ответ ещё не записан — не судим
        self.assertIsNone(self.verdict(self.apply()))

    def test_fail_open_and_subagent(self) -> None:
        code, output, _ = guard(shell(self.apply(), self.root))
        self.assertEqual((code, output), (0, None))
        code, output, _ = guard(shell(self.apply(), self.root, self.tmp / "missing.jsonl"))
        self.assertEqual((code, output), (0, None))
        self.human("да")
        self.assertEqual(self.verdict(self.apply(), agent_id="agent-1", agent_type="other-plugin:helper"), "deny")

    def test_gate_speakers_review(self) -> None:
        gate = f"{self.NA} source gate --meeting {MEETING} --consent yes --masking none --sensitivity confidential --routes local_and_cloud_llm --retain-source yes"
        self.human("Внутренняя встреча, участники уведомлены, confidential, без маскирования, хранить")
        self.assertIsNone(self.verdict(gate))
        self.entries.clear()
        self.human("Согласия участников нет")
        self.assertEqual(self.verdict(gate), "deny")
        speakers = f'{self.NA} speakers set --meeting {MEETING} --map "Speaker 1=Анна Крылова (закупки);Speaker 2=Олег Данилов"'
        self.entries.clear()
        self.human("Speaker 2 — это Олег Данилов из NordTech")
        self.assertIsNone(self.verdict(speakers))
        self.entries.clear()
        self.human("Продолжай разбор")
        self.assertEqual(self.verdict(speakers), "deny")
        review = f'{self.NA} review record --item rq-1 --decision "Сергей — ЛПР" --status closed'
        self.entries.clear()
        self.human("Сергей и есть ЛПР")
        self.assertIsNone(self.verdict(review))

    def test_send_after_preview_and_codex_transcript(self) -> None:
        self.ran(f"{self.NA} deliver preview --file r.md --channel telegram")
        self.human("Отправляй")
        self.assertIsNone(self.verdict(f"{self.NA} deliver send --file r.md --confirm"))
        self.entries = [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "да, применяй"}]}}]
        self.assertIsNone(self.verdict(self.apply()))
        self.entries = [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "покажи, что изменится"}]}}]
        self.assertEqual(self.verdict(self.apply()), "deny")


class PluginComponentsTests(unittest.TestCase):
    def test_agents_frontmatter(self) -> None:
        agents = sorted((REPO / "agents").glob("*.md"))
        names = set()
        for path in agents:
            data, body = frontmatter(path)
            self.assertTrue(set(data) <= PLUGIN_AGENT_FIELDS, f"{path.name}: неподдерживаемые поля {set(data) - PLUGIN_AGENT_FIELDS}")
            self.assertEqual(data.get("name"), path.stem)
            self.assertNotIn(":", data["name"])
            self.assertTrue(isinstance(data.get("description"), str) and 40 <= len(data["description"]) <= 600, path.name)
            tools = data.get("tools")
            listed = tools if isinstance(tools, list) else [item.strip() for item in str(tools).split(",") if item.strip()]
            self.assertTrue(set(listed) <= KNOWN_TOOLS, f"{path.name}: {listed}")
            self.assertGreater(len(body.strip()), 200, path.name)
            names.add(data["name"])
        self.assertTrue({"transcript-analyst", "evidence-verifier", "reader"} <= names)
        reader, _ = frontmatter(REPO / "agents" / "reader.md")
        self.assertEqual(reader["tools"], [])
        verifier, _ = frontmatter(REPO / "agents" / "evidence-verifier.md")
        self.assertFalse({"Write", "Edit", "Bash"} & {item.strip() for item in verifier["tools"].split(",")})
        analyst, _ = frontmatter(REPO / "agents" / "transcript-analyst.md")
        analyst_tools = {item.strip() for item in analyst["tools"].split(",")}
        self.assertTrue({"Read", "Write"} <= analyst_tools)
        self.assertNotIn("Bash", analyst_tools)

    def test_hooks_json(self) -> None:
        config = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertTrue(set(config) <= {"description", "hooks"})
        groups = config["hooks"]["PreToolUse"]
        self.assertTrue(groups)
        for group in groups:
            tokens = set(group["matcher"].split("|"))
            self.assertTrue({"Write", "Edit", "NotebookEdit", "Bash", "apply_patch"} <= tokens)
            for handler in group["hooks"]:
                self.assertEqual(handler["type"], "command")
                script = handler["args"][0]
                self.assertTrue(script.startswith("${CLAUDE_PLUGIN_ROOT}/"))
                self.assertTrue((REPO / script.replace("${CLAUDE_PLUGIN_ROOT}/", "")).is_file())
                self.assertLessEqual(handler.get("timeout", 600), 30)
        py_compile.compile(str(HOOK), doraise=True)

    def test_skill_descriptions(self) -> None:
        for path in sorted((REPO / "skills").glob("*/SKILL.md")):
            data, _ = frontmatter(path)
            description = data.get("description")
            self.assertTrue(isinstance(description, str) and description.strip(), path)
            self.assertLessEqual(len(description), 1024, f"{path}: описание длиннее 1024 знаков")
            self.assertNotIn(", а не ", description, f"{path}: противопоставление в описании")


if __name__ == "__main__":
    unittest.main(verbosity=2)
