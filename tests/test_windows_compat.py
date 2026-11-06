"""Регрессии Windows: пути, запуск защитных проверок и сохранность байтов карточки."""
from __future__ import annotations

import codecs
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest import mock

from run_tests import REPO, WorkspaceTestCase

sys.path.insert(0, str(REPO / "hooks"))
from _na_hooklib import tokenize
from na_engine import doctor, workspace_schema


class WindowsPathTests(WorkspaceTestCase):
    def test_native_paths_keep_separators(self):
        cases = [
            (r'rm -rf C:\folder\workspace', 'C:/folder/workspace'),
            (r'rm -rf "C:\Каталог с пробелами\workspace"', 'C:/Каталог с пробелами/workspace'),
            (r"rm -rf '\\server\share\folder name\workspace'", '//server/share/folder name/workspace'),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(tokenize(command)[-1], expected)
        self.assertEqual(tokenize(r'ROOT=C:\folder\workspace; printf x > $ROOT/knowledge/index.md')[0], 'ROOT=C:/folder/workspace')

    def test_posix_escapes_remain_intact(self):
        self.assertEqual(tokenize(r'printf x > folder\ with\ spaces/index.md')[-1], 'folder with spaces/index.md')
        self.assertEqual(tokenize(r'''python -c 'print("a\\nb")' ''')[2], r'print("a\\nb")')

    def test_migration_preserves_lf_crlf_and_bom(self):
        card = self.root / 'knowledge/people/p-test.md'
        card.parent.mkdir(parents=True)
        for newline in ('\n', '\r\n'):
            for bom in (b'', codecs.BOM_UTF8):
                with self.subTest(newline=repr(newline), bom=bool(bom)):
                    original = bom + newline.join(['---', 'id: p-test', '---', '(m) Ручная пометка', '']).encode('utf-8')
                    card.write_bytes(original)
                    plan = workspace_schema._plan_name_variants(self.root)
                    workspace_schema._apply_name_variants(self.root, plan)
                    after = card.read_bytes()
                    retained = b''.join(line for line in after.splitlines(keepends=True) if not line.startswith((b'speech_variants:', b'asr_variants:')))
                    self.assertEqual(retained, original)
                    self.assertEqual(workspace_schema._plan_name_variants(self.root)['changes'], [])


class HookLauncherTests(WorkspaceTestCase):
    def bash(self):
        return doctor.hook_shell()

    def call_launcher(self, *args, env=None, data='{}'):
        return subprocess.run([self.bash(), str(REPO / 'hooks/run-python.sh'), *args], input=data,
                              capture_output=True, text=True, encoding='utf-8', env=env, timeout=15)

    def fake_python(self, name, body):
        path = self.tmp / 'bin' / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(('#!/bin/sh\n' + body + '\n').encode('utf-8'))
        path.chmod(0o755)
        return path

    def fake_env(self):
        return {**os.environ, 'PATH': str(self.tmp / 'bin'), 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}

    def test_missing_python3_falls_back_without_losing_input(self):
        self.fake_python('python3', 'exit 127')
        executable = shlex.quote(sys.executable.replace('\\', '/'))
        self.fake_python('python', f'exec {executable} "$@"')
        (self.root / '.na-workspace').touch()
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Write', 'cwd': str(self.root),
                 'tool_input': {'file_path': str(self.root / 'knowledge/people/человек.md'), 'content': 'x'}}
        result = self.call_launcher(str(REPO / 'hooks/guard_writes.py'), env=self.fake_env(), data=json.dumps(event))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertFalse((self.root / 'knowledge').exists())

    def test_store_stub_with_zero_exit_does_not_count_as_python(self):
        self.fake_python('python3', 'printf "Microsoft Store\\n"; exit 0')
        executable = shlex.quote(sys.executable.replace('\\', '/'))
        self.fake_python('python', f'exec {executable} "$@"')
        result = self.call_launcher(str(REPO / 'hooks/guard_writes.py'), env=self.fake_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_py_launcher_is_final_fallback(self):
        for name in ('python3', 'python'):
            self.fake_python(name, 'exit 127')
        executable = shlex.quote(sys.executable.replace('\\', '/'))
        self.fake_python('py', f'shift; exec {executable} "$@"')
        result = self.call_launcher(str(REPO / 'hooks/guard_writes.py'), env=self.fake_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_old_python3_is_skipped(self):
        self.fake_python('python3', 'printf NA_PYTHON_OLD')
        executable = shlex.quote(sys.executable.replace('\\', '/'))
        self.fake_python('python', f'exec {executable} "$@"')
        result = self.call_launcher(str(REPO / 'hooks/guard_writes.py'), env=self.fake_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_no_interpreter_reports_failure(self):
        for name in ('python3', 'python', 'py'):
            self.fake_python(name, 'exit 127')
        result = self.call_launcher(str(REPO / 'hooks/guard_writes.py'), env=self.fake_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('Python', result.stderr)

    def test_configured_handler_really_denies_write(self):
        (self.root / '.na-workspace').touch()
        config = json.loads((REPO / 'hooks/hooks.json').read_text(encoding='utf-8'))
        handler = config['hooks']['PreToolUse'][0]['hooks'][0]
        self.assertNotIn('args', handler)
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Write', 'cwd': str(self.root),
                 'tool_input': {'file_path': str(self.root / 'knowledge/people/x.md')}}
        result = subprocess.run([self.bash(), '-c', handler['command']], input=json.dumps(event),
                                capture_output=True, text=True, encoding='utf-8',
                                env={**os.environ, 'CLAUDE_PLUGIN_ROOT': str(REPO), 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_doctor_probes_configured_handler_not_python3_name(self):
        probes = [{'command': 'python3', 'works': False}, {'command': 'python', 'works': True}]
        with mock.patch.object(doctor, 'probe_interpreters', return_value=probes), \
             mock.patch.object(doctor, 'probe_hooks', return_value=[{'works': True, 'detail': ''}]):
            checked = doctor.run_doctor(self.root, probe=True)
        self.assertNotIn('hooks', checked['errors'])
        with mock.patch.object(doctor, 'probe_interpreters', return_value=probes), \
             mock.patch.object(doctor, 'probe_hooks', return_value=[{'works': False, 'detail': 'launcher failed'}]):
            checked = doctor.run_doctor(self.root, probe=True)
        self.assertIn('hooks', checked['errors'])
