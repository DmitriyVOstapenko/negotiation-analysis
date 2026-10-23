"""Патч Ивана (tools/ivan_patch.py): наложение по якорям, идемпотентность, признак рабочей папки."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "ivan_patch.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("ivan_patch", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_stub_ivan(root: Path) -> None:
    """Минимальное дерево Ивана 2.7.3 с теми же якорями, что и в поставке."""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "coman-ivan-cowork", "version": "2.7.3"}), encoding="utf-8")
    hooks = root / "hooks"
    hooks.mkdir()
    (hooks / "check_closing_handoff.py").write_text(
        "import json, sys\n\ndef main():\n    try:\n        hook_input = json.loads(sys.stdin.read())\n"
        "    except (json.JSONDecodeError, ValueError):\n        return\n\n    transcript = load_messages(hook_input)\n",
        encoding="utf-8")
    (hooks / "post_write_md_check.py").write_text(
        "import os\n\ndef main():\n    hook_input = {}\n    file_path = ''\n\n    basename = os.path.basename(file_path)\n"
        "    if basename in (SKIP_FILENAMES | _env_set(\"IVAN_MD_SKIP_FILES\")):\n        return\n",
        encoding="utf-8")
    (hooks / "session_start_load_rules.py").write_text(
        "import json, os, sys\n\ndef main() -> None:\n    root = os.getcwd()\n", encoding="utf-8")
    for skill in ("close-session", "ivan-rules"):
        (root / "skills" / skill).mkdir(parents=True)
        (root / "skills" / skill / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: test\n---\n\n# {skill}\n\nТело навыка.\n", encoding="utf-8")


class IvanPatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "coman-ivan-cowork"
        make_stub_ivan(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_apply_is_anchored_and_idempotent(self) -> None:
        self.assertFalse(self.tool.check(self.root))
        first = dict(self.tool.apply(self.root))
        self.assertEqual(first["hooks/_na_workspace.py"], "written")
        self.assertTrue(all(state == "patched" for name, state in first.items() if name != "hooks/_na_workspace.py"))
        self.assertTrue(self.tool.check(self.root))
        second = dict(self.tool.apply(self.root))
        self.assertTrue(all(state == "already" for state in second.values()))
        # патч стоит перед load_messages и после конца frontmatter
        handoff = (self.root / "hooks" / "check_closing_handoff.py").read_text(encoding="utf-8")
        self.assertLess(handoff.index("_na_workspace.in_workspace"), handoff.index("transcript = load_messages"))
        skill = (self.root / "skills" / "ivan-rules" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        self.assertLess(skill.index("\n---\n"), skill.index("negotiation-analysis patch"))
        self.assertLess(skill.index("negotiation-analysis patch"), skill.index("# ivan-rules"))
        for name in ("check_closing_handoff.py", "post_write_md_check.py", "session_start_load_rules.py", "_na_workspace.py"):
            compile((self.root / "hooks" / name).read_text(encoding="utf-8"), name, "exec")

    def test_unknown_layout_is_refused(self) -> None:
        (self.root / "hooks" / "check_closing_handoff.py").write_text("def main():\n    pass\n", encoding="utf-8")
        with self.assertRaises(LookupError):
            self.tool.apply(self.root)

    def test_workspace_detection(self) -> None:
        self.tool.apply(self.root)
        sys.path.insert(0, str(self.root / "hooks"))
        try:
            import _na_workspace  # noqa: F401
            importlib.reload(_na_workspace)
        finally:
            sys.path.pop(0)
        base = Path(self.tmp.name)
        ws = base / "Переговоры"
        (ws / "knowledge").mkdir(parents=True)
        (ws / "negotiations" / "m").mkdir(parents=True)
        (ws / "knowledge" / "workspace.json").write_text(json.dumps({"workspace_format": "negotiation-analysis-workspace"}), encoding="utf-8")
        foreign = base / "foreign"
        (foreign / "knowledge").mkdir(parents=True)
        (foreign / "knowledge" / "workspace.json").write_text(json.dumps({"workspace_format": "something-else"}), encoding="utf-8")
        self.assertEqual(_na_workspace.workspace_root(ws), str(ws))
        self.assertEqual(_na_workspace.workspace_root(ws / "negotiations" / "m"), str(ws))
        self.assertEqual(_na_workspace.workspace_root(ws / "negotiations" / "m" / "report.md"), str(ws))
        self.assertEqual(_na_workspace.workspace_root(base), str(ws), "подключена родительская папка — рабочая папка на уровень ниже")
        self.assertIsNone(_na_workspace.workspace_root(foreign))
        self.assertIsNone(_na_workspace.workspace_root(foreign / "x" / "y.md"))
        self.assertTrue(_na_workspace.in_workspace({"cwd": str(ws)}))
        self.assertTrue(_na_workspace.in_workspace({"cwd": str(foreign)}, path=str(ws / "negotiations" / "m" / "report.md")))
        self.assertFalse(_na_workspace.in_workspace({"cwd": str(foreign)}))


if __name__ == "__main__":
    unittest.main()
