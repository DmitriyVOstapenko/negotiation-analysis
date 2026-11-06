"""Codex package: discoverable workflows, portable paths, identity and no personal patch."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


class CodexPackageTests(unittest.TestCase):
    def test_entrypoints_retain_complete_workflows_within_loading_budget(self) -> None:
        skills = sorted((REPO / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(skills), 7)
        for entry in skills:
            with self.subTest(skill=entry.parent.name):
                raw = entry.read_bytes()
                self.assertLessEqual(len(raw), 8000)
                text = raw.decode("utf-8")
                self.assertIn("целиком", text)
                self.assertIn("references/workflow.md", text)
                self.assertIn("docs/CODEX.md", text)
                workflow = entry.parent / "references" / "workflow.md"
                self.assertTrue(workflow.is_file())
                self.assertIn("Назначение", workflow.read_text(encoding="utf-8"))
                self.assertIn("решения", text.lower())
                # Every rendered local link must resolve from the actual file location.
                for source in (entry, workflow):
                    for link in re.findall(r"\]\(([^)]+)\)", source.read_text(encoding="utf-8")):
                        if "://" in link or link.startswith("#"):
                            continue
                        target = (source.parent / link.split("#", 1)[0]).resolve()
                        self.assertTrue(target.is_relative_to(REPO), str(target))
                        self.assertTrue(target.exists(), f"{source}: {link}")

    def test_host_manifests_agree_and_codex_paths_stay_in_package(self) -> None:
        codex = json.loads((REPO / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        listing = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["plugins"][0]
        self.assertEqual(codex["name"], claude["name"])
        self.assertEqual(codex["version"], claude["version"])
        self.assertEqual(codex["version"], listing["version"])
        for field in ("skills", "hooks"):
            relative = codex[field]
            self.assertIsInstance(relative, str)
            self.assertTrue(relative.startswith("./"), relative)
            self.assertNotIn("..", Path(relative).parts)
            target = (REPO / relative).resolve()
            self.assertTrue(target.is_relative_to(REPO))
            self.assertTrue(target.exists())
        self.assertTrue((REPO / codex["skills"]).is_dir())

    def test_public_package_has_no_ivan_patch_dependency(self) -> None:
        self.assertFalse((REPO / "tools" / "ivan_patch.py").exists())
        self.assertFalse((REPO / "tests" / "test_ivan_patch.py").exists())
        # Runtime workflows and installation hooks must not tell a new user to apply it.
        paths = list((REPO / "skills").rglob("SKILL.md"))
        paths += list((REPO / "skills").rglob("workflow.md"))
        paths += list((REPO / "hooks").glob("*.json"))
        for path in paths:
            self.assertNotIn("ivan_patch.py", path.read_text(encoding="utf-8"), str(path))

    def test_archive_manifest_covers_codex_runtime_and_moved_workflows(self) -> None:
        spec = importlib.util.spec_from_file_location("na_build_manifest", REPO / "tools" / "build_manifest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        files = [REPO / ".codex-plugin" / "plugin.json", REPO / "hooks" / "codex.json", REPO / "docs" / "CODEX.md"]
        files += sorted((REPO / "skills").glob("*/SKILL.md"))
        files += sorted((REPO / "skills").glob("*/references/workflow.md"))
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "codex.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for path in files:
                    archive.write(path, path.relative_to(REPO).as_posix())
            manifest = module.manifest_from_zip(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(set(manifest["files"]), set(archive.namelist()))
                for name, fingerprint in manifest["files"].items():
                    self.assertEqual(fingerprint["sha256"], hashlib.sha256(archive.read(name)).hexdigest())
            for entry in (REPO / "skills").glob("*/SKILL.md"):
                self.assertIn("body_sha256", manifest["files"][entry.relative_to(REPO).as_posix()])


if __name__ == "__main__":
    unittest.main()
