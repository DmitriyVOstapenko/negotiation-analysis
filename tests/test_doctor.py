"""Самопроверка установки (na.py doctor), паспорт сборки, сканер секретов, защита ключа бота."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from run_tests import REPO, WorkspaceTestCase, payload, run

sys.path.insert(0, str(REPO / "scripts"))
from na_engine import doctor  # noqa: E402

SCAN = REPO / "tools" / "secret_scan.py"


class TestDoctor(WorkspaceTestCase):
    def test_doctor_reports_plugin_and_workspace(self) -> None:
        (self.root / "knowledge").mkdir()
        result = payload(run(self.root, "doctor"))
        self.assertIn(result["checks"]["python"]["state"], ("ok", "error"))
        self.assertEqual(result["checks"]["workspace"]["state"] in ("ok", "warning"), True)
        self.assertIn("schema", result["checks"]["workspace"])
        self.assertEqual(result["checks"]["agents"]["state"] in ("ok", "absent"), True)

    def test_manifest_detects_changed_files_and_ignores_skill_header(self) -> None:
        plugin = Path(tempfile.mkdtemp(prefix="na-plugin-"))
        try:
            (plugin / "skills" / "x").mkdir(parents=True)
            skill = plugin / "skills" / "x" / "SKILL.md"
            skill.write_text("---\nname: x\ndescription: старое\n---\n\nТело навыка\n", encoding="utf-8")
            other = plugin / "a.txt"
            other.write_text("один", encoding="utf-8")
            manifest = {"version": "t", "files": {"skills/x/SKILL.md": doctor.file_fingerprint(skill), "a.txt": doctor.file_fingerprint(other)}}
            (plugin / doctor.MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(doctor.check_manifest(plugin)["state"], "ok")
            skill.write_text("---\nname: x\ndescription: переписано синхронизацией\n---\n\nТело навыка\n", encoding="utf-8")
            self.assertEqual(doctor.check_manifest(plugin)["state"], "ok")
            other.write_text("два", encoding="utf-8")
            checked = doctor.check_manifest(plugin)
            self.assertEqual(checked["state"], "changed")
            self.assertEqual(checked["changed"], ["a.txt"])
        finally:
            import shutil

            shutil.rmtree(plugin, ignore_errors=True)

    def test_neighbor_ivan_detected_by_directory_name(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="na-synced-"))
        try:
            ours = base / "id-ours" / "negotiation-analysis"
            ours.mkdir(parents=True)
            (base / "id-ivan" / "coman-ivan-cowork").mkdir(parents=True)
            self.assertIn("coman-ivan-cowork", doctor.find_neighbors(ours))
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)

    def test_hooks_json_paths_checked(self) -> None:
        plugin = Path(tempfile.mkdtemp(prefix="na-hooks-"))
        try:
            (plugin / "hooks").mkdir()
            (plugin / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/missing.py"]}]}]}}), encoding="utf-8")
            self.assertEqual(doctor.check_hooks(plugin)["state"], "error")
            (plugin / "hooks" / "missing.py").write_text("", encoding="utf-8")
            self.assertEqual(doctor.check_hooks(plugin)["state"], "ok")
        finally:
            import shutil

            shutil.rmtree(plugin, ignore_errors=True)

    def test_delivery_dir_gets_gitignore_for_secrets(self) -> None:
        env = {**os.environ, "PYTHONUTF8": "1"}
        token = "123456789:" + "B" * 35  # secret-scan: allow
        completed = subprocess.run([sys.executable, str(REPO / "scripts" / "na.py"), "--root", str(self.root), "deliver", "setup", "--telegram-token-stdin"], input=token + "\n", capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        ignore = (self.root / "delivery" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("secrets/", ignore)


class TestSecretScan(unittest.TestCase):
    def test_telegram_token_is_caught_and_masked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak.md"
            token = "123456789:" + "C" * 35  # secret-scan: allow
            path.write_text(f"ключ: {token}\n", encoding="utf-8")
            completed = subprocess.run([sys.executable, str(SCAN), str(path)], capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("telegram-bot-token", completed.stdout)
            self.assertNotIn(token, completed.stdout)

    def test_repository_has_no_secrets(self) -> None:
        if not (REPO / ".git").exists():
            self.skipTest("пакет без git")
        completed = subprocess.run([sys.executable, str(SCAN), "--all"], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_package_manifest_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "p.plugin"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("skills/x/SKILL.md", "---\nname: x\n---\nтело\n")
                archive.writestr("scripts/a.py", "print(1)\n")
            completed = subprocess.run([sys.executable, str(REPO / "tools" / "build_manifest.py"), "--from-zip", str(package), "--append"], capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read("BUILD-MANIFEST.json"))
            self.assertEqual(sorted(manifest["files"]), ["scripts/a.py", "skills/x/SKILL.md"])
            self.assertIn("body_sha256", manifest["files"]["skills/x/SKILL.md"])


if __name__ == "__main__":
    unittest.main()
