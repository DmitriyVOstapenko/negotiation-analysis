"""Regressions from live use, with synthetic names and meeting identifiers only."""
import json
import unittest

import test_codex_hooks


class OwnerContinuityTests(unittest.TestCase):
    setUp = test_codex_hooks.CodexHookTests.setUp
    tearDown = test_codex_hooks.CodexHookTests.tearDown
    verdict = test_codex_hooks.CodexHookTests.verdict
    human = staticmethod(test_codex_hooks.CodexHookTests.human)
    call = staticmethod(test_codex_hooks.CodexHookTests.call)
    GATE = 'python scripts/na.py source gate --meeting sample-a --consent yes --masking none --sensitivity internal --routes local_and_cloud_llm --retain-source yes'
    MAP = 'python scripts/na.py speakers set --meeting sample-a --map "Speaker 1=Анна;Speaker 2=Борис"'

    def answer(self, question, answer):
        return self.human('<send_user_message_question_reply>\n' + json.dumps([
            {'questionItemId': '["request_user_input_async","test",0]', 'question': question, 'answer': answer}
        ], ensure_ascii=False) + '\n</send_user_message_question_reply>')

    def gate_history(self):
        return [self.human('Согласие получено, облачная обработка разрешена, без маскирования, хранить'),
                self.call('python scripts/na.py source create --meeting sample-a --transcript source.txt')]

    def test_gate_survives_status_question(self):
        self.assertIsNone(self.verdict('exec_command', {'cmd': self.GATE}, self.gate_history()+[self.human('Ты здесь?')]))
        self.assertIsNone(self.verdict('exec_command', {'cmd': self.GATE}, self.gate_history()+[self.human('А аудио ты можешь послушать или нет?')]))

    def test_async_answer_does_not_include_negative_question_words(self):
        reply=self.answer('Согласие получено; конфиденциальных данных нет; облачная обработка разрешена?', 'Да, подтверждаю все условия')
        self.assertIsNone(self.verdict('exec_command', {'cmd': self.GATE}, [reply]))

    def test_negative_button_answer_cannot_become_consent(self):
        reply=self.answer('Согласие получено, облачная обработка разрешена?', 'Согласия нет')
        self.assertEqual(self.verdict('exec_command', {'cmd': self.GATE}, [reply]), 'deny')

    def test_speaker_confirmation_survives_status_question(self):
        entries=[self.call('python scripts/na.py source create --meeting sample-a --transcript source.txt'),
                 self.answer('Подтвердите: Speaker 1 — Анна, Speaker 2 — Борис', 'Да, карту подтверждаю'),
                 self.human('Ты здесь?')]
        self.assertIsNone(self.verdict('exec_command', {'cmd': self.MAP}, entries))
        swapped=self.MAP.replace('Speaker 1=Анна;Speaker 2=Борис','Speaker 1=Борис;Speaker 2=Анна')
        self.assertEqual(self.verdict('exec_command', {'cmd': swapped}, entries), 'deny')
        entries += [self.human('Нет, Speaker 1 не Анна, карта неверна'),self.human('Ты здесь?')]
        self.assertEqual(self.verdict('exec_command', {'cmd': self.MAP}, entries), 'deny')

    def test_revocation_overrides_previous_gate(self):
        for text in ['Согласия нет', 'Не разрешаю обработку', 'Стоп', 'Нет']:
            self.assertEqual(self.verdict('exec_command', {'cmd': self.GATE}, self.gate_history()+[self.human(text)]), 'deny')

    def test_partial_question_preserves_prior_self_identification(self):
        entries=[self.human('Я Speaker 1'), self.call('python scripts/na.py source create --meeting sample-a --transcript source.txt'),
                 self.answer('Подтвердите: Speaker 2 — Борис', 'Да, подтверждаю'),self.human('Ты здесь?')]
        self.assertIsNone(self.verdict('exec_command',{'cmd':self.MAP},entries))
        self.assertEqual(self.verdict('exec_command',{'cmd':self.MAP.replace('Speaker 2=Борис','Speaker 2=Анна')},entries),'deny')
        self.assertEqual(self.verdict('exec_command',{'cmd':self.MAP.replace('Борис"','Борис;Speaker 3=Вера"')},entries),'deny')

    def test_native_code_mode_command_events_bind_the_meeting(self):
        native={'type':'event_msg','payload':{'type':'item_completed','item':{'type':'CommandExecution',
            'id':'native-1','command':['pwsh.exe','-Command','python scripts/na.py source create --meeting sample-a --transcript source.txt'],
            'cwd':str(self.root)}}}
        entries=[self.human('Я Speaker 1'),native,self.answer('Подтвердите: Speaker 2 — Борис','Да, подтверждаю'),self.human('Ты здесь?')]
        self.assertIsNone(self.verdict('exec_command',{'cmd':self.MAP},entries))

    def test_other_meeting_and_other_root_do_not_inherit_consent(self):
        for call in ['python scripts/na.py source create --meeting sample-b --transcript b.txt',
                     'python scripts/na.py --root elsewhere source create --meeting sample-a --transcript b.txt']:
            entries=self.gate_history()+[self.call(call),self.human('Ты здесь?')]
            self.assertEqual(self.verdict('exec_command', {'cmd': self.GATE}, entries), 'deny')

    def test_changed_gate_payload_requires_new_approval(self):
        entries=self.gate_history()+[self.call(self.GATE),self.human('Ты здесь?')]
        self.assertEqual(self.verdict('exec_command', {'cmd': self.GATE.replace('--masking none','--masking full')},entries),'deny')

    def test_approval_to_process_is_not_approval_to_apply_or_send(self):
        for command in ['python scripts/na.py knowledge apply --meeting sample-a --confirm',
                        'python scripts/na.py deliver send --file report.md --confirm']:
            self.assertEqual(self.verdict('exec_command', {'cmd': command},self.gate_history()+[self.human('Ты здесь?')]),'deny')


if __name__=='__main__':
    unittest.main()
