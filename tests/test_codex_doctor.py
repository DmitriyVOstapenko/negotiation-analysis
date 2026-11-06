"""Реальный запуск объявленного Codex-обработчика без Bash и выбор конфигурации."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from run_tests import REPO, WorkspaceTestCase
from na_engine import doctor


class CodexDoctorTests(WorkspaceTestCase):
    def test_declared_native_handler_denies_real_write_event(self):
        (self.root / '.na-workspace').touch()
        config = json.loads((REPO / 'hooks/codex.json').read_text(encoding='utf-8'))
        handler = config['hooks']['PreToolUse'][0]['hooks'][0]
        command = handler['commandWindows' if os.name == 'nt' else 'command']
        command = command.replace('${PLUGIN_ROOT}', str(REPO))
        argv = command if os.name == 'nt' else shlex.split(command)
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'cwd': str(self.root),
                 'tool_input': {'command': 'Set-Content knowledge/people/x.md changed' if os.name == 'nt' else 'echo changed > knowledge/people/x.md'}}
        result = subprocess.run(argv, input=json.dumps(event), capture_output=True, text=True,
                                encoding='utf-8', cwd=self.root, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertFalse((self.root / 'knowledge/people/x.md').exists())

    def test_doctor_probes_selected_config_without_bash(self):
        self.assertEqual(doctor.hook_config_path(REPO), REPO / 'hooks/codex.json')
        with mock.patch.object(doctor, 'hook_shell', side_effect=AssertionError('Bash must not run')):
            probes = doctor.probe_hooks(REPO)
        self.assertTrue(probes)
        self.assertTrue(all(item['works'] for item in probes), probes)
        self.assertEqual(doctor.check_hooks(REPO)['state'], 'ok')

    def test_selected_missing_script_is_reported(self):
        (self.root / '.codex-plugin').mkdir()
        (self.root / 'hooks').mkdir()
        (self.root / '.codex-plugin/plugin.json').write_text(json.dumps({'hooks': './hooks/codex.json'}), encoding='utf-8')
        self.assertEqual(doctor.check_hooks(self.root)['state'], 'error')
        (self.root / 'hooks/codex.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'hooks': [
            {'type': 'command', 'command': 'python3 "${PLUGIN_ROOT}/hooks/missing.py"'}]}]}}), encoding='utf-8')
        self.assertEqual(doctor.check_hooks(self.root)['state'], 'error')

    def test_zero_exit_without_handler_output_is_not_success(self):
        stub = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with mock.patch.object(doctor.subprocess, 'run', return_value=stub):
            probes = doctor.probe_hooks(REPO)
        self.assertFalse(probes[0]['works'])

    def test_claude_config_can_be_selected_explicitly(self):
        self.assertEqual(doctor.hook_config_path(REPO, 'claude'), REPO / 'hooks/hooks.json')
        self.assertEqual(doctor.check_hooks(REPO, 'claude')['state'], 'ok')
        self.assertEqual(doctor.check_hooks(self.root, 'codex')['state'], 'error')


if __name__ == '__main__':
    unittest.main()
