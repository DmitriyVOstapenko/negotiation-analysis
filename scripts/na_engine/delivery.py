"""Доставка материалов самому пользователю: Telegram (свой бот) и почта (через подключение Gmail).

Решение владельца 2026-10-19: отправлять можно только себе и можно отправлять всё.
Правила, которые здесь держит код:

- получатель один — сам пользователь: адрес почты и личный чат Telegram из ``delivery/config.json``;
  других адресатов команды не принимают;
- отправить можно любой файл рабочей папки, кроме ``delivery/`` (настройки, ключи, журнал);
- сначала ``preview`` (показ: кому, что, сколько частей, отпечаток), потом ``send --confirm``
  для Telegram или ``record`` после действия в почте; содержимое сверяется по хешу показа;
- журнал ``delivery/journal.jsonl`` только дописывается: канал, адресат, файл, отпечаток, исход;
- исход неизвестен или отправлено частично → повтор того же текста заблокирован, пока
  пользователь не проверит чат (``--resend-unknown``); уже отправленное — только с ``--again``;
- в сеть ходят только команды Telegram (``telegram-link`` и ``send``), только к API Telegram.
  Почту плагин сам не отправляет: письмо создаёт подключение Gmail в Claude, команда лишь
  готовит показ и пишет журнал.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .common import NaError, append_jsonl, gate_state, now_iso, read_json_or_none, read_jsonl, relative, write_json

DELIVERY_DIR = "delivery"
CONFIG_FILE = "delivery/config.json"
JOURNAL_FILE = "delivery/journal.jsonl"
PENDING_DIR = "delivery/pending"
TOKEN_FILE = "delivery/secrets/telegram_bot_token"
HOME_TOKEN_FILE = Path("~/.config/negotiation-analysis/telegram_bot_token")
TOKEN_ENV = "NA_TELEGRAM_BOT_TOKEN"
API_BASE_ENV = "NA_TELEGRAM_API_BASE"
TIMEOUT_ENV = "NA_TELEGRAM_TIMEOUT"
DEFAULT_API_BASE = "https://api.telegram.org"

CHANNELS = ("telegram", "email")
MODES = ("auto", "text", "file")
RECORD_STATUSES = ("draft", "sent", "failed", "unknown")

MESSAGE_LIMIT = 4096  # предел Telegram на одно сообщение
CHUNK_SOURCE_LIMIT = 3000  # кусок исходного текста до разметки
AUTO_TEXT_MAX_PARTS = 3  # длиннее — файлом
CAPTION_LIMIT = 1000  # предел подписи к файлу 1024
DOCUMENT_LIMIT = 50 * 1024 * 1024  # предел ботов на отправку файла
PENDING_TTL = timedelta(hours=24)

_EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv"}


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------


def _config_path(root: Path) -> Path:
    return root / CONFIG_FILE


def load_config(root: Path) -> dict[str, Any]:
    data = read_json_or_none(_config_path(root))
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("recipient", "self")
    data.setdefault("email_self", None)
    data.setdefault("telegram", None)
    return data


IGNORE_TEXT = "# ключ бота и ожидающие показы не попадают в git, даже если папка под git\nsecrets/\npending/\n"


def ensure_ignore(root: Path) -> None:
    """delivery/.gitignore — чтобы ключ бота не ушёл в репозиторий, если рабочая папка под git."""
    path = root / DELIVERY_DIR / ".gitignore"
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(IGNORE_TEXT, encoding="utf-8")


def _save_config(root: Path, config: dict[str, Any]) -> None:
    config["updated_at"] = now_iso()
    write_json(_config_path(root), config)
    ensure_ignore(root)


def _token_source(root: Path) -> tuple[str | None, str]:
    env = (os.environ.get(TOKEN_ENV) or "").strip()
    if env:
        return env, f"переменная {TOKEN_ENV}"
    for path, label in ((root / TOKEN_FILE, TOKEN_FILE), (HOME_TOKEN_FILE.expanduser(), str(HOME_TOKEN_FILE))):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text, label
    return None, "не найден"


def _require_token(root: Path) -> str:
    token, _ = _token_source(root)
    if not token:
        raise NaError(
            "Ключ бота Telegram не найден. Создайте бота у @BotFather и сохраните ключ: "
            f"переменная {TOKEN_ENV}, файл {TOKEN_FILE} в рабочей папке "
            "(команда deliver setup --telegram-token-stdin) или ~/.config/negotiation-analysis/telegram_bot_token."
        )
    if not _TOKEN_RE.match(token):
        raise NaError("Ключ бота Telegram не похож на ключ от @BotFather (вид «123456:ABC…»). Проверьте файл ключа.")
    return token


def setup(root: Path, email: str | None, token_stdin: str | None, forget_telegram: bool, forget_email: bool) -> dict[str, Any]:
    if not any((email, token_stdin is not None, forget_telegram, forget_email)):
        raise NaError("Нечего настраивать: укажите --email, --telegram-token-stdin, --forget-telegram или --forget-email")
    config = load_config(root)
    changes: list[str] = []
    if email:
        email = email.strip()
        if not _EMAIL_RE.match(email):
            raise NaError(f"Адрес почты выглядит некорректно: {email}")
        config["email_self"] = email
        changes.append(f"адрес почты для отправки себе: {email}")
    if forget_email:
        config["email_self"] = None
        changes.append("адрес почты забыт")
    if token_stdin is not None:
        token = token_stdin.strip()
        if not _TOKEN_RE.match(token):
            raise NaError("Ключ бота не похож на ключ от @BotFather (вид «123456:ABC…»); ничего не записано")
        path = root / TOKEN_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_ignore(root)
        path.write_text(token + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        changes.append(f"ключ бота сохранён в {TOKEN_FILE} (сам ключ не показывается)")
    if forget_telegram:
        config["telegram"] = None
        path = root / TOKEN_FILE
        if path.exists():
            path.unlink()
            changes.append("личный чат Telegram отвязан, файл ключа в рабочей папке удалён")
        else:
            changes.append("личный чат Telegram отвязан; файла ключа в рабочей папке не было")
    _save_config(root, config)
    return {"config": _public_config(root, config), "changes": changes}


def _public_config(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    _, token_from = _token_source(root)
    return {
        "recipient": "self",
        "email_self": config.get("email_self"),
        "telegram": config.get("telegram"),
        "telegram_token": token_from,
    }


# ---------------------------------------------------------------------------
# Telegram: сеть
# ---------------------------------------------------------------------------


class _Unknown(Exception):
    """Запрос мог дойти, а ответа нет: исход неизвестен."""


class _Failed(Exception):
    """Сервер ответил отказом или соединение не установилось: точно не доставлено."""


def _api_base() -> str:
    return (os.environ.get(API_BASE_ENV) or DEFAULT_API_BASE).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.environ.get(TIMEOUT_ENV) or 30)
    except ValueError:
        return 30.0


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "<ключ скрыт>") if token else text


def _call(token: str, method: str, *, payload: dict[str, Any] | None = None, multipart: tuple[bytes, str] | None = None) -> Any:
    url = f"{_api_base()}/bot{token}/{method}"
    if multipart is not None:
        body, content_type = multipart
        request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type}, method="POST")
    elif payload is not None:
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    else:
        request = urllib.request.Request(url, method="GET")
    try:
        with _opener(url).open(request, timeout=_timeout()) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            description = data.get("description") or str(exc)
        except Exception:  # noqa: BLE001 — тело ответа не JSON
            description = str(exc)
        message = _scrub(f"Telegram ответил {exc.code}: {description}", token)
        if exc.code >= 500:
            raise _Unknown(message) from None
        raise _Failed(message) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLCertVerificationError)):
            raise _Failed(_scrub(f"Нет соединения с Telegram: {reason}", token)) from None
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise _Unknown(_scrub(f"Telegram не ответил вовремя: {reason}", token)) from None
        raise _Unknown(_scrub(f"Связь с Telegram прервалась: {reason}", token)) from None
    except (TimeoutError, socket.timeout) as exc:
        raise _Unknown(_scrub(f"Telegram не ответил вовремя: {exc}", token)) from None
    except (ConnectionError, OSError) as exc:
        raise _Unknown(_scrub(f"Связь с Telegram прервалась: {exc}", token)) from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Unknown("Telegram вернул ответ, который не удалось прочитать") from None
    if not data.get("ok"):
        raise _Failed(_scrub(f"Telegram отказал: {data.get('description')}", token))
    return data.get("result")


def _multipart(fields: dict[str, str], file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "na-" + secrets.token_hex(12)
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    safe_name = filename.replace('"', "'")
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{safe_name}\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _offline(exc: Exception) -> NaError:
    return NaError(f"{exc}. Ничего не записано в настройки.", exit_code=1)


def telegram_link(root: Path, chat_id: int | None, confirm: bool) -> dict[str, Any]:
    token = _require_token(root)
    try:
        bot = _call(token, "getMe")
        updates = _call(token, "getUpdates", payload={"limit": 100, "allowed_updates": ["message"]})
    except (_Failed, _Unknown) as exc:
        raise _offline(exc) from None
    candidates: dict[int, dict[str, Any]] = {}
    for update in updates or []:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
            continue
        name = " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part) or None
        candidates[chat["id"]] = {"chat_id": chat["id"], "name": name, "username": chat.get("username")}
    bot_name = f"@{bot.get('username')}" if isinstance(bot, dict) and bot.get("username") else None
    result: dict[str, Any] = {"bot": bot_name, "candidates": list(candidates.values()), "linked": None}
    if not confirm:
        return result
    if chat_id is None:
        raise NaError("Для привязки укажите --chat <id> из списка кандидатов и --confirm")
    if chat_id not in candidates:
        raise NaError(
            f"Чат {chat_id} не найден среди личных чатов, писавших боту {bot_name or ''}. "
            "Напишите боту любое сообщение (например /start) со своего аккаунта и повторите."
        )
    config = load_config(root)
    config["telegram"] = {**candidates[chat_id], "bot": bot_name, "linked_at": now_iso()}
    _save_config(root, config)
    result["linked"] = config["telegram"]
    return result


# ---------------------------------------------------------------------------
# Подготовка текста
# ---------------------------------------------------------------------------


def _resolve_file(root: Path, file_arg: str) -> Path:
    path = Path(file_arg).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        inside = path.relative_to(root.resolve())
    except ValueError:
        raise NaError("Отправлять можно только файлы рабочей папки") from None
    if inside.parts and inside.parts[0] == DELIVERY_DIR:
        raise NaError("Файлы delivery/ (настройки, ключи, журнал) не отправляются")
    if not path.is_file():
        raise NaError(f"Файл не найден: {relative(path, root)}")
    return path


def _sha256_bytes(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _is_text(path: Path, content: bytes) -> str | None:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def split_text(text: str, limit: int = CHUNK_SOURCE_LIMIT) -> list[str]:
    """Разбить по абзацам, не разрывая абзац без нужды; таблицы и блоки кода — целиком, если влезают."""
    text = text.strip("\n")
    if not text:
        return []
    blocks = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        pieces = [block] if len(block) <= limit else _hard_split(block, limit)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _hard_split(block: str, limit: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for line in block.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = line
    if current:
        pieces.append(current)
    return pieces


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def markdown_to_telegram_html(chunk: str) -> str:
    """Небольшое подмножество Markdown → HTML Telegram: заголовки, жирный, код, таблицы и блоки кода — моноширинно."""
    out: list[str] = []
    lines = chunk.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            body: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                body.append(lines[index])
                index += 1
            index += 1
            out.append(f"<pre>{_escape(chr(10).join(body))}</pre>")
            continue
        if _TABLE_ROW.match(line):
            body = []
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                body.append(lines[index].strip())
                index += 1
            out.append(f"<pre>{_escape(chr(10).join(body))}</pre>")
            continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if heading:
            out.append(f"<b>{_inline(heading.group(1).strip())}</b>")
        else:
            quote = re.match(r"^\s{0,3}>\s?(.*)$", line)
            out.append(f"<blockquote>{_inline(quote.group(1))}</blockquote>" if quote else _inline(line))
        index += 1
    return "\n".join(out)


def _inline(text: str) -> str:
    parts = re.split(r"(`[^`\n]+`)", text)
    rendered: list[str] = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            rendered.append(f"<code>{_escape(part[1:-1])}</code>")
            continue
        escaped = _escape(part)
        escaped = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)
        rendered.append(escaped)
    return "".join(rendered)


def _caption(text: str | None, filename: str) -> str:
    if not text:
        return filename
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = next((re.sub(r"^#+\s*", "", line) for line in lines if line.startswith("#")), None) or filename
    caption = f"{title}\n\nПолный текст — во вложении ({filename})."
    return caption[:CAPTION_LIMIT]


def _plan(path: Path, content: bytes, channel: str, mode: str) -> dict[str, Any]:
    text = _is_text(path, content)
    if channel == "email":
        if text is None:
            raise NaError("В письмо уходит текст: выберите текстовый файл (.md, .txt, .json…) или отправьте файл в Telegram")
        title = next((re.sub(r"^#+\s*", "", line.strip()) for line in text.splitlines() if line.strip().startswith("#")), None)
        return {"mode": "text", "parts": 1, "subject": title or path.name, "chars": len(text)}
    if mode == "text" and text is None:
        raise NaError("Текстом можно отправить только текстовый файл в UTF-8; выберите --mode file")
    chunks = split_text(text) if text is not None else []
    if mode == "auto":
        mode = "text" if text is not None and 0 < len(chunks) <= AUTO_TEXT_MAX_PARTS else "file"
    if mode == "text" and not chunks:
        raise NaError("Файл пустой — отправлять нечего")
    if mode == "file" and len(content) > DOCUMENT_LIMIT:
        raise NaError("Файл больше 50 МБ — бот Telegram такой не отправит")
    return {"mode": mode, "parts": len(chunks) if mode == "text" else 1, "chars": len(text) if text is not None else None}


# ---------------------------------------------------------------------------
# Показ, отправка, журнал
# ---------------------------------------------------------------------------


def _recipient(config: dict[str, Any], channel: str) -> dict[str, Any]:
    if channel == "email":
        address = config.get("email_self")
        if not address:
            raise NaError("Адрес почты для отправки себе не задан: deliver setup --email <ваш адрес>")
        return {"recipient": "self", "address": address}
    telegram = config.get("telegram")
    if not isinstance(telegram, dict) or not isinstance(telegram.get("chat_id"), int):
        raise NaError("Личный чат Telegram не привязан: deliver telegram-link (сначала напишите боту /start)")
    if telegram["chat_id"] <= 0:
        raise NaError("Привязан не личный чат: отправка разрешена только себе. Привяжите личный чат заново")
    label = telegram.get("name") or (f"@{telegram['username']}" if telegram.get("username") else str(telegram["chat_id"]))
    return {"recipient": "self", "address": telegram["chat_id"], "label": label, "bot": telegram.get("bot")}


def _pending_path(root: Path, sha: str, channel: str) -> Path:
    return root / PENDING_DIR / f"{sha[:16]}-{channel}.json"


def _gate_notes(root: Path, path: Path) -> list[str]:
    try:
        inside = path.relative_to(root.resolve())
    except ValueError:
        return []
    if len(inside.parts) < 2 or inside.parts[0] != "negotiations":
        return []
    meeting_path = root / "negotiations" / inside.parts[1]
    state = gate_state(meeting_path)
    notes: list[str] = []
    if state.get("allowed_routes") == "local_only":
        notes.append(f"встреча {inside.parts[1]} помечена local_only — отправка вынесет её материалы за пределы вашего компьютера")
    if state.get("sensitivity") in ("confidential", "regulated"):
        notes.append(f"встреча {inside.parts[1]}: чувствительность {state['sensitivity']}")
    return notes


def _history(root: Path, channel: str, sha: str) -> dict[str, Any] | None:
    last = None
    for record in read_jsonl(root / JOURNAL_FILE):
        if record.get("channel") == channel and record.get("sha256") == sha:
            last = record
    return last


def preview(root: Path, file_arg: str, channel: str, mode: str = "auto") -> dict[str, Any]:
    if channel not in CHANNELS:
        raise NaError(f"Канал: {', '.join(CHANNELS)}")
    config = load_config(root)
    recipient = _recipient(config, channel)
    path = _resolve_file(root, file_arg)
    content = path.read_bytes()
    sha = _sha256_bytes(content)
    plan = _plan(path, content, channel, mode)
    previous = _history(root, channel, sha)
    notes = _gate_notes(root, path)
    record = {
        "file": relative(path, root),
        "sha256": sha,
        "bytes": len(content),
        "channel": channel,
        **recipient,
        **plan,
        "previewed_at": now_iso(),
        "previous": previous,
        "notes": notes,
    }
    write_json(_pending_path(root, sha, channel), record)
    ensure_ignore(root)
    return record


def _load_pending(root: Path, path: Path, channel: str) -> tuple[dict[str, Any], bytes, str]:
    content = path.read_bytes()
    sha = _sha256_bytes(content)
    pending_path = _pending_path(root, sha, channel)
    pending = read_json_or_none(pending_path)
    if not isinstance(pending, dict) or pending.get("sha256") != sha:
        raise NaError(
            "Нет показа для этого содержимого: сначала deliver preview (файл мог измениться после показа). Ничего не отправлено.",
            exit_code=1,
        )
    try:
        previewed = datetime.fromisoformat(pending["previewed_at"])
        if datetime.now(timezone.utc) - previewed > PENDING_TTL:
            raise NaError("Показ устарел (больше суток): покажите заново — deliver preview. Ничего не отправлено.", exit_code=1)
    except (KeyError, ValueError):
        raise NaError("Показ повреждён: повторите deliver preview. Ничего не отправлено.", exit_code=1) from None
    return pending, content, sha


def _guard_repeat(root: Path, channel: str, sha: str, again: bool, resend_unknown: bool) -> None:
    previous = _history(root, channel, sha)
    if not previous:
        return
    status = previous.get("status")
    if status in ("unknown", "partial") and not resend_unknown:
        raise NaError(
            f"Прошлая отправка этого же текста ({previous.get('at')}) закончилась с неизвестным исходом ({status}). "
            "Проверьте чат: если сообщения нет — повторите с --resend-unknown. Ничего не отправлено.",
            exit_code=1,
        )
    if status in ("sent", "draft") and not again:
        raise NaError(
            f"Этот же текст уже {'отправлен' if status == 'sent' else 'положен в черновики'} {previous.get('at')}. "
            "Ещё одна копия — только с --again. Ничего не отправлено.",
            exit_code=1,
        )


def _journal(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    entry = {"id": f"dlv-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{record['sha256'][:8]}", "at": now_iso(), **record}
    append_jsonl(root / JOURNAL_FILE, [entry])
    return entry


def send(root: Path, file_arg: str, channel: str, confirm: bool, again: bool = False, resend_unknown: bool = False) -> dict[str, Any]:
    if channel != "telegram":
        raise NaError("Сам плагин отправляет только в Telegram. Письмо создаёт подключение Gmail; затем запишите исход: deliver record")
    if not confirm:
        raise NaError("Отправка только с --confirm, после показа (deliver preview) и вашего «отправить». Ничего не отправлено.")
    path = _resolve_file(root, file_arg)
    pending, content, sha = _load_pending(root, path, channel)
    config = load_config(root)
    recipient = _recipient(config, channel)
    if recipient["address"] != pending.get("address"):
        raise NaError("После показа сменился привязанный чат: покажите заново. Ничего не отправлено.", exit_code=1)
    _guard_repeat(root, channel, sha, again, resend_unknown)
    token = _require_token(root)
    chat_id = recipient["address"]
    mode = pending.get("mode") or "file"
    sent_ids: list[int] = []
    parts_total = 1
    status = "sent"
    error = None
    try:
        if mode == "text":
            chunks = split_text(content.decode("utf-8"))
            parts_total = len(chunks)
            for chunk in chunks:
                html = markdown_to_telegram_html(chunk)
                payload: dict[str, Any] = {"chat_id": chat_id, "disable_web_page_preview": True}
                if len(html) <= MESSAGE_LIMIT:
                    payload.update(text=html, parse_mode="HTML")
                else:
                    payload.update(text=chunk[:MESSAGE_LIMIT])
                try:
                    message = _call(token, "sendMessage", payload=payload)
                except _Failed as exc:
                    if "parse_mode" not in payload or "entities" not in str(exc):
                        raise
                    # разметка не разобрана Telegram — тот же кусок простым текстом (первый запрос точно отклонён)
                    message = _call(token, "sendMessage", payload={"chat_id": chat_id, "text": chunk[:MESSAGE_LIMIT], "disable_web_page_preview": True})
                sent_ids.append(int(message.get("message_id")) if isinstance(message, dict) and message.get("message_id") is not None else -1)
        else:
            text = _is_text(path, content)
            body = _multipart({"chat_id": str(chat_id), "caption": _caption(text, path.name)}, "document", path.name, content)
            message = _call(token, "sendDocument", multipart=body)
            sent_ids.append(int(message.get("message_id")) if isinstance(message, dict) and message.get("message_id") is not None else -1)
    except _Failed as exc:
        status = "partial" if sent_ids else "failed"
        error = str(exc)
    except _Unknown as exc:
        status = "unknown" if not sent_ids else "partial"
        error = str(exc)
    entry = _journal(
        root,
        {
            "channel": channel,
            "recipient": "self",
            "address": chat_id,
            "label": recipient.get("label"),
            "file": pending["file"],
            "sha256": sha,
            "bytes": len(content),
            "mode": mode,
            "parts": parts_total,
            "parts_sent": len(sent_ids),
            "message_ids": sent_ids,
            "status": status,
            "error": error,
        },
    )
    if status in ("sent", "failed"):
        _pending_path(root, sha, channel).unlink(missing_ok=True)
    return entry


def record(root: Path, file_arg: str, channel: str, status: str, ref: str | None, again: bool = False) -> dict[str, Any]:
    if channel != "email":
        raise NaError("deliver record — для почты: исход записывается после действия подключения Gmail. Telegram записывается сам при send")
    if status not in RECORD_STATUSES:
        raise NaError(f"Исход: {', '.join(RECORD_STATUSES)}")
    path = _resolve_file(root, file_arg)
    pending, content, sha = _load_pending(root, path, channel)
    config = load_config(root)
    recipient = _recipient(config, channel)
    if recipient["address"] != pending.get("address"):
        raise NaError("После показа сменился адрес почты: покажите заново", exit_code=1)
    if status in ("draft", "sent"):
        _guard_repeat(root, channel, sha, again, resend_unknown=True)
    entry = _journal(
        root,
        {
            "channel": channel,
            "recipient": "self",
            "address": recipient["address"],
            "file": pending["file"],
            "sha256": sha,
            "bytes": len(content),
            "mode": "text",
            "subject": pending.get("subject"),
            "status": status,
            "ref": ref,
        },
    )
    if status in ("draft", "sent", "failed"):
        _pending_path(root, sha, channel).unlink(missing_ok=True)
    return entry


def journal(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    records = read_jsonl(root / JOURNAL_FILE)
    return records[-limit:] if limit > 0 else records


def status(root: Path) -> dict[str, Any]:
    config = load_config(root)
    if (root / DELIVERY_DIR).is_dir():
        ensure_ignore(root)
    pending_dir = root / PENDING_DIR
    pending = sorted(p.name for p in pending_dir.glob("*.json")) if pending_dir.is_dir() else []
    return {"config": _public_config(root, config), "pending": pending, "recent": journal(root, 5)}
