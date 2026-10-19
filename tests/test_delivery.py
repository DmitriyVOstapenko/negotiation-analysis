"""Отправка себе: настройки, показ, подтверждение, защита от повторов, только личный чат, журнал."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from run_tests import NA, REPO, WorkspaceTestCase, payload, run

sys.path.insert(0, str(REPO / "scripts"))
from na_engine import delivery  # noqa: E402

TOKEN = "123456:" + "A" * 35
CHAT_ID = 777001


class FakeTelegram(BaseHTTPRequestHandler):
    server_version = "FakeTelegram/1"

    def log_message(self, *args: object) -> None:  # тишина в выводе тестов
        return

    def _reply(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self) -> None:
        state = self.server.state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        method = self.path.rsplit("/", 1)[-1]
        if f"/bot{TOKEN}/" not in self.path:
            self._reply(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
            return
        content_type = self.headers.get("Content-Type") or ""
        data = json.loads(body.decode("utf-8")) if body and content_type.startswith("application/json") else {}
        state["calls"].append({"method": method, "data": data, "raw": body})
        if method == "getMe":
            self._reply(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "test_na_bot"}})
            return
        if method == "getUpdates":
            self._reply(200, {"ok": True, "result": [
                {"update_id": 1, "message": {"message_id": 1, "chat": {"id": CHAT_ID, "type": "private", "first_name": "Дмитрий", "username": "owner"}, "text": "/start"}},
                {"update_id": 2, "message": {"message_id": 2, "chat": {"id": -100500, "type": "group", "title": "Чужая группа"}, "text": "привет"}},
            ]})
            return
        if method in ("sendMessage", "sendDocument"):
            if state.get("timeout_once"):
                state["timeout_once"] = False
                time.sleep(2.0)
            if state.get("reject_html") and data.get("parse_mode") == "HTML":
                self._reply(400, {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities: unexpected end tag"})
                return
            state["counter"] += 1
            self._reply(200, {"ok": True, "result": {"message_id": state["counter"]}})
            return
        self._reply(404, {"ok": False, "description": "Not Found"})

    do_GET = _handle
    do_POST = _handle


class DeliveryTestCase(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
        self.server.state = {"calls": [], "counter": 100}  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.saved_env = {key: os.environ.get(key) for key in ("NA_TELEGRAM_API_BASE", "NA_TELEGRAM_BOT_TOKEN", "NA_TELEGRAM_TIMEOUT")}
        os.environ["NA_TELEGRAM_API_BASE"] = f"http://127.0.0.1:{self.server.server_address[1]}"
        os.environ["NA_TELEGRAM_BOT_TOKEN"] = TOKEN
        os.environ["NA_TELEGRAM_TIMEOUT"] = "5"
        (self.root / "negotiations" / "m1").mkdir(parents=True)
        self.report = self.root / "negotiations" / "m1" / "report.md"
        self.report.write_text("# Отчёт о встрече\n\n**Итог:** договорились о сроке <15 ноября> & цене.\n\n| Кто | Что |\n|---|---|\n| Анна | смета |\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    @property
    def calls(self) -> list[dict]:
        return self.server.state["calls"]  # type: ignore[attr-defined]

    def link(self) -> None:
        completed = run(self.root, "deliver", "telegram-link", "--chat", str(CHAT_ID), "--confirm")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def journal(self) -> list[dict]:
        path = self.root / "delivery" / "journal.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


class TestDeliverySetup(DeliveryTestCase):
    def test_link_lists_only_private_chats_and_refuses_group(self) -> None:
        listed = payload(run(self.root, "deliver", "telegram-link"))
        self.assertEqual([item["chat_id"] for item in listed["candidates"]], [CHAT_ID])
        refused = run(self.root, "deliver", "telegram-link", "--chat", "-100500", "--confirm")
        self.assertNotEqual(refused.returncode, 0)
        self.link()
        config = json.loads((self.root / "delivery" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["telegram"]["chat_id"], CHAT_ID)
        self.assertEqual(config["telegram"]["bot"], "@test_na_bot")

    def test_token_from_stdin_is_stored_privately_and_never_printed(self) -> None:
        env = {**os.environ, "PYTHONUTF8": "1"}
        env.pop("NA_TELEGRAM_BOT_TOKEN", None)
        completed = subprocess.run([sys.executable, str(NA), "--root", str(self.root), "--json", "deliver", "setup", "--telegram-token-stdin"], input=TOKEN + "\n", capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn(TOKEN, completed.stdout + completed.stderr)
        token_file = self.root / "delivery" / "secrets" / "telegram_bot_token"
        self.assertEqual(token_file.read_text(encoding="utf-8").strip(), TOKEN)
        if os.name == "posix":
            self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
        bad = subprocess.run([sys.executable, str(NA), "--root", str(self.root), "deliver", "setup", "--telegram-token-stdin"], input="не ключ\n", capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        self.assertNotEqual(bad.returncode, 0)

    def test_email_needs_valid_address(self) -> None:
        self.assertNotEqual(run(self.root, "deliver", "setup", "--email", "не адрес").returncode, 0)
        self.assertEqual(run(self.root, "deliver", "setup", "--email", "owner@example.org").returncode, 0)


class TestTelegramSend(DeliveryTestCase):
    def test_send_requires_preview_and_confirm(self) -> None:
        self.link()
        no_preview = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm")
        self.assertEqual(no_preview.returncode, 1)
        shown = payload(run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "telegram"))
        self.assertEqual(shown["address"], CHAT_ID)
        self.assertEqual(shown["mode"], "text")
        no_confirm = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md")
        self.assertNotEqual(no_confirm.returncode, 0)
        self.assertFalse([call for call in self.calls if call["method"] == "sendMessage"])
        sent = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm")
        self.assertEqual(sent.returncode, 0, sent.stdout + sent.stderr)
        messages = [call["data"] for call in self.calls if call["method"] == "sendMessage"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["chat_id"], CHAT_ID)
        self.assertEqual(messages[0]["parse_mode"], "HTML")
        self.assertIn("<b>Отчёт о встрече</b>", messages[0]["text"])
        self.assertIn("&lt;15 ноября&gt; &amp;", messages[0]["text"])
        self.assertIn("<pre>| Кто | Что |", messages[0]["text"])
        record = self.journal()[-1]
        self.assertEqual((record["status"], record["recipient"], record["address"]), ("sent", "self", CHAT_ID))
        self.assertNotIn(TOKEN, (self.root / "delivery" / "journal.jsonl").read_text(encoding="utf-8"))

    def test_repeat_needs_again_and_changed_file_needs_new_preview(self) -> None:
        self.link()
        run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "telegram")
        self.assertEqual(run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm").returncode, 0)
        run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "telegram")
        repeat = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm")
        self.assertEqual(repeat.returncode, 1)
        self.assertEqual(run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm", "--again").returncode, 0)
        run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "telegram")
        self.report.write_text(self.report.read_text(encoding="utf-8") + "\nдописано после показа\n", encoding="utf-8")
        changed = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm", "--again")
        self.assertEqual(changed.returncode, 1)
        self.assertIn("показ", (changed.stderr + changed.stdout).lower())

    def test_unknown_outcome_blocks_blind_resend(self) -> None:
        self.link()
        os.environ["NA_TELEGRAM_TIMEOUT"] = "0.5"
        self.server.state["timeout_once"] = True  # type: ignore[attr-defined]
        run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "telegram")
        first = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm")
        self.assertEqual(first.returncode, 1)
        self.assertEqual(self.journal()[-1]["status"], "unknown")
        os.environ["NA_TELEGRAM_TIMEOUT"] = "5"
        blind = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm", "--again")
        self.assertEqual(blind.returncode, 1)
        checked = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm", "--resend-unknown")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(self.journal()[-1]["status"], "sent")

    def test_html_rejected_falls_back_to_plain_text(self) -> None:
        self.link()
        self.server.state["reject_html"] = True  # type: ignore[attr-defined]
        run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "telegram")
        sent = run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--confirm")
        self.assertEqual(sent.returncode, 0, sent.stdout + sent.stderr)
        plain = [call["data"] for call in self.calls if call["method"] == "sendMessage" and "parse_mode" not in call["data"]]
        self.assertEqual(len(plain), 1)
        self.assertIn("**Итог:**", plain[0]["text"])

    def test_long_text_goes_as_file_with_caption(self) -> None:
        self.link()
        long_path = self.root / "negotiations" / "m1" / "long.md"
        long_path.write_text("# Большой отчёт\n\n" + "\n\n".join(f"Абзац {i}: " + "слово " * 120 for i in range(40)), encoding="utf-8")
        shown = payload(run(self.root, "deliver", "preview", "--file", "negotiations/m1/long.md", "--channel", "telegram"))
        self.assertEqual(shown["mode"], "file")
        sent = run(self.root, "deliver", "send", "--file", "negotiations/m1/long.md", "--confirm")
        self.assertEqual(sent.returncode, 0, sent.stdout + sent.stderr)
        documents = [call for call in self.calls if call["method"] == "sendDocument"]
        self.assertEqual(len(documents), 1)
        raw = documents[0]["raw"].decode("utf-8", errors="replace")
        self.assertIn(str(CHAT_ID), raw)
        self.assertIn("Большой отчёт", raw)
        self.assertIn('filename="long.md"', raw)

    def test_only_workspace_files_and_not_delivery_dir(self) -> None:
        self.link()
        outside = self.tmp / "outside.md"
        outside.write_text("# чужое\n", encoding="utf-8")
        self.assertNotEqual(run(self.root, "deliver", "preview", "--file", str(outside), "--channel", "telegram").returncode, 0)
        self.assertNotEqual(run(self.root, "deliver", "preview", "--file", "delivery/config.json", "--channel", "telegram").returncode, 0)

    def test_without_linked_chat_nothing_goes(self) -> None:
        completed = run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "telegram")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.calls)


class TestEmailRecord(DeliveryTestCase):
    def test_email_preview_record_and_repeat_guard(self) -> None:
        self.assertNotEqual(run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "email").returncode, 0)
        run(self.root, "deliver", "setup", "--email", "owner@example.org")
        shown = payload(run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "email"))
        self.assertEqual(shown["address"], "owner@example.org")
        self.assertEqual(shown["subject"], "Отчёт о встрече")
        self.assertNotEqual(run(self.root, "deliver", "send", "--file", "negotiations/m1/report.md", "--channel", "email", "--confirm").returncode, 0)
        recorded = run(self.root, "deliver", "record", "--file", "negotiations/m1/report.md", "--status", "draft", "--ref", "r-1")
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertEqual(self.journal()[-1]["status"], "draft")
        run(self.root, "deliver", "preview", "--file", "negotiations/m1/report.md", "--channel", "email")
        self.assertEqual(run(self.root, "deliver", "record", "--file", "negotiations/m1/report.md", "--status", "sent").returncode, 1)
        self.assertEqual(run(self.root, "deliver", "record", "--file", "negotiations/m1/report.md", "--status", "sent", "--again").returncode, 0)


class TestTelegramMarkup(unittest.TestCase):
    def test_markdown_subset(self) -> None:
        html = delivery.markdown_to_telegram_html("## Риски\n> цитата <важно>\n`код & ещё`\n[ссылка](https://example.org)")
        self.assertIn("<b>Риски</b>", html)
        self.assertIn("<blockquote>цитата &lt;важно&gt;</blockquote>", html)
        self.assertIn("<code>код &amp; ещё</code>", html)
        self.assertIn('<a href="https://example.org">ссылка</a>', html)

    def test_split_keeps_paragraphs_under_limit(self) -> None:
        text = "\n\n".join("абзац " * 100 for _ in range(10))
        chunks = delivery.split_text(text, limit=1500)
        self.assertTrue(all(len(chunk) <= 1500 for chunk in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), text.replace("\n", ""))


if __name__ == "__main__":
    unittest.main()
