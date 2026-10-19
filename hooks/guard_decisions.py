#!/usr/bin/env python3
"""PreToolUse-хук плагина negotiation-analysis: команды na.py, которые фиксируют решение владельца,
выполняются только после его ответа.

Зачем. Другие плагины в той же сессии могут подталкивать модель «решать самой вместо вопроса» —
тогда модель сама поставит --confirm. Хук сверяет такую команду с последним вводом человека в
транскрипте сессии (transcript_path): последняя его реплика или ответ на AskUserQuestion.

Какие команды (вызов scripts/na.py в команде оболочки):
- knowledge apply --confirm; knowledge decide (кроме --list); migrate --confirm;
- source gate (любой --consent); speakers set; review record; review confirm;
- deliver send --confirm; deliver record --status sent; deliver telegram-link --confirm.

Что считается ответом владельца:
- для применения и отправки (apply, migrate, review confirm, send, record sent, telegram-link) —
  явное согласие («да», «применяй», «подтверждаю», «согласен», «отправляй», «ок», «верно» …) без
  отказа или отсрочки («нет», «стоп», «подожди», «сначала …», «не применяй»); если в транскрипте
  был показ (knowledge diff, deliver preview, план migrate, список telegram-link), согласие должно
  прозвучать после последнего показа;
- для шлюза — согласие или ответы хотя бы на два вопроса шлюза (конфиденциальность, маскирование,
  маршрут, хранение, уведомление участников); --consent yes при прямом «согласия нет» — отказ;
- для карты спикеров — согласие или имя из --map в реплике;
- для review record — согласие или общие слова реплики и --decision;
- для knowledge decide — согласие, слова выбранного решения (принять, отклонить, переписать,
  объединить) или общие слова с --text.

Чего хук не делает. Транскрипта нет, он не читается, человеческой реплики нет или вопрос
AskUserQuestion ещё без ответа — решения нет (fail-open): хук не судит то, чего не видит, а правила
навыков действуют как прежде. Отказ — только когда реплика прочитана уверенно и ответа владельца в
ней нет. Из субагента (agent_id во входе) такие команды не выполняются: решение владельца
принимает главный разговор.

Ответ — JSON PreToolUse (permissionDecision: deny) с кодом 0; пустой вывод — решения нет.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _na_hooklib import last_human_input, na_invocations  # noqa: E402

SHELL_TOOL_RE = re.compile(r"(?i)bash|shell|powershell|exec")
VALUE_OPTIONS = {"--root", "--meeting", "--map", "--decision", "--status", "--item", "--text", "--target", "--file", "--channel",
                 "--consent", "--masking", "--sensitivity", "--routes", "--retain-source", "--ref", "--chat", "--mode", "--profile"}

CONSENT_WORDS = {
    "да", "ага", "угу", "ок", "окей", "ok", "okay", "yes", "go", "конечно", "верно", "правильно", "хорошо", "годится",
    "подходит", "поехали", "вперед", "валяй", "давай", "давайте", "принимаю", "принято", "принять", "одобряю", "разрешаю",
    "согласен", "согласна", "согласны", "отправил", "отправила", "отправлено", "можно",
}
CONSENT_STEMS = ("примен", "подтвер", "соглас", "отправ", "выполн", "сохран", "запиш", "записыв", "внос", "внеси", "закрыв",
                 "закрой", "делай", "сделай", "утверд", "одобр", "разреш", "мигрир", "привяз")
REFUSAL_WORDS = {"нет", "стоп", "подожди", "подождите", "погоди", "погодите", "постой", "отмена", "отмени", "отменить", "отложи",
                 "отложим", "рано", "нельзя", "запрещаю", "сначала", "no", "stop", "wait"}
GATE_STEMS = ("соглас", "уведом", "разреш", "конфиденц", "внутрен", "публичн", "регулир", "маскир", "инициал", "псевдоним",
              "хран", "локальн", "облач", "confidential", "internal", "public", "regulated", "initials", "pseudonym", "mask")
GATE_REFUSAL_RE = re.compile(r"(?:нет|без)(?:\s+\w+){0,2}\s+согласи|согласи\w*(?:\s+\w+){0,3}\s+нет\b|не\s+соглас|не\s+разреш|не\s+уведомл")
DECIDE_STEMS = {
    "accept": ("приня", "приним", "остав", "вноси", "внести", "соглас"),
    "reject": ("отклон", "убер", "убрать", "не вносить", "не внос", "удал", "нет"),
    "rewrite": ("перепиш", "исправ", "формулир", "переформ"),
    "merge": ("объедин", "слей", "слить", "совмест"),
}
STOPWORDS = {"и", "в", "во", "на", "не", "по", "с", "со", "к", "о", "об", "а", "то", "это", "что", "как", "для", "из", "за", "от", "у", "же", "ли"}
STRICT_KINDS = {"apply", "migrate", "confirm", "send", "sent", "link"}
# Показ, после которого владелец отвечает: вызов na.py (слова команды) без --confirm.
SHOW_CALLS = {
    "apply": ("knowledge", "diff"),
    "send": ("deliver", "preview"),
    "migrate": ("migrate",),
    "link": ("deliver", "telegram-link"),
}


def is_show_command(kind: str, command: str) -> bool:
    """Есть ли в команде оболочки вызов na.py, который показывает владельцу предмет решения."""
    if kind not in SHOW_CALLS or "na.py" not in command:
        return False
    wanted = SHOW_CALLS[kind]
    for args in na_invocations(command):
        words, options = parse_call(args)
        if tuple(words[: len(wanted)]) == wanted and not options.get("--confirm"):
            return True
    return False
LABELS = {
    "apply": "knowledge apply --confirm (применение изменений базы)",
    "decide": "knowledge decide (решение по пункту учёта)",
    "migrate": "migrate --confirm (миграция рабочей папки)",
    "gate": "source gate (решение шлюза по записи)",
    "speakers": "speakers set (карта спикеров)",
    "review": "review record (решение по пункту очереди)",
    "confirm": "review confirm (статус проверки встречи)",
    "send": "deliver send --confirm (отправка)",
    "sent": "deliver record --status sent (отметка об отправке)",
    "link": "deliver telegram-link --confirm (привязка чата)",
}


def normalize(text: str) -> list[str]:
    return re.sub(r"[^\w]+", " ", text.casefold().replace("ё", "е")).split()


def focus(text: str) -> str:
    """Своё сообщение человека: без цитат и блоков кода; у длинного — только начало и конец."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    if len(text) > 1200:
        text = text[:400] + "\n" + text[-400:]
    return text


def has_consent(tokens: list[str]) -> bool:
    for position, token in enumerate(tokens):
        if token in CONSENT_WORDS or token.startswith(CONSENT_STEMS):
            if position > 0 and tokens[position - 1] in ("не", "нет"):
                continue
            return True
    return False


NOT_REFUSAL_AFTER_NO = {"проблем", "проблема", "возражений", "вопросов", "сомнений", "претензий"}


def has_refusal(tokens: list[str]) -> bool:
    for position, token in enumerate(tokens):
        if token in REFUSAL_WORDS:
            # «нет проблем, применяй» — согласие, а не отказ
            if token == "нет" and position + 1 < len(tokens) and tokens[position + 1] in NOT_REFUSAL_AFTER_NO:
                continue
            return True
    return any(tokens[position - 1] == "не" and (token in CONSENT_WORDS or token.startswith(CONSENT_STEMS)) for position, token in enumerate(tokens) if position)


def parse_call(args: list[str]) -> tuple[list[str], dict[str, object]]:
    words: list[str] = []
    options: dict[str, object] = {}
    position = 0
    while position < len(args):
        arg = args[position]
        if arg.startswith("--"):
            if "=" in arg:
                key, value = arg.split("=", 1)
                options[key] = value
            elif arg in VALUE_OPTIONS and position + 1 < len(args):
                options[arg] = args[position + 1]
                position += 1
            else:
                options[arg] = True
        elif not arg.startswith("-"):
            words.append(arg)
        position += 1
    return words, options


def classify_call(args: list[str]) -> tuple[str, dict[str, object]] | None:
    words, options = parse_call(args)
    head = tuple(words[:2])
    if head == ("knowledge", "apply") and options.get("--confirm"):
        return "apply", options
    if head == ("knowledge", "decide") and not options.get("--list"):
        return "decide", options
    if words[:1] == ["migrate"] and options.get("--confirm"):
        return "migrate", options
    if head == ("source", "gate"):
        return "gate", options
    if head == ("speakers", "set"):
        return "speakers", options
    if head == ("review", "record"):
        return "review", options
    if head == ("review", "confirm"):
        return "confirm", options
    if head == ("deliver", "send") and options.get("--confirm"):
        return "send", options
    if head == ("deliver", "record") and str(options.get("--status", "")).lower() == "sent":
        return "sent", options
    if head == ("deliver", "telegram-link") and options.get("--confirm"):
        return "link", options
    return None


def _overlap(reply: list[str], text: object) -> bool:
    if not isinstance(text, str):
        return False
    wanted = {token for token in normalize(text) if len(token) >= 2 and token not in STOPWORDS}
    return bool(wanted & set(reply))


def evidence(kind: str, options: dict[str, object], reply_text: str) -> tuple[bool, str]:
    tokens = normalize(reply_text)
    consent = has_consent(tokens)
    if kind in STRICT_KINDS:
        if has_refusal(tokens):
            return False, "в реплике отказ или просьба подождать"
        return consent, "явного согласия нет"
    if kind == "gate":
        joined = " ".join(tokens)
        if str(options.get("--consent", "")).lower() == "yes" and GATE_REFUSAL_RE.search(joined):
            return False, "в реплике сказано, что согласия нет, а команда записывает --consent yes"
        answered = {stem for stem in GATE_STEMS for token in tokens if token.startswith(stem)}
        return consent or len(answered) >= 2, "ответов на вопросы шлюза нет"
    if kind == "speakers":
        names = []
        for part in str(options.get("--map", "")).split(";"):
            if "=" in part:
                names += [token for token in normalize(part.split("=", 1)[1].split("(", 1)[0]) if len(token) >= 3]
        return consent or bool(set(names) & set(tokens)), "ни согласия с картой, ни имён из неё нет"
    if kind == "review":
        return consent or _overlap(tokens, options.get("--decision")), "ни согласия, ни слов решения нет"
    if kind == "decide":
        decision = str(options.get("--decision", ""))
        stems = DECIDE_STEMS.get(decision, ())
        joined = " ".join(tokens)
        named = any((stem in joined) if " " in stem else (stem == "нет" and stem in tokens) or (stem != "нет" and any(token.startswith(stem) for token in tokens)) for stem in stems)
        return consent or named or _overlap(tokens, options.get("--text")), "ни согласия, ни выбранного решения нет"
    return True, ""


def decide(event: dict) -> dict | None:
    if not isinstance(event, dict) or event.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    tool = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool, str) or not SHELL_TOOL_RE.search(tool) or not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or "na.py" not in command:
        return None
    calls = [found for found in (classify_call(args) for args in na_invocations(command)) if found]
    if not calls:
        return None
    if event.get("agent_id"):
        kinds = ", ".join(LABELS[kind] for kind, _ in calls)
        return _deny(f"negotiation-analysis: {kinds} — решение владельца; из субагента не выполняется. Верните вопрос в главный разговор.")
    reasons: list[str] = []
    for kind, options in calls:
        if is_show_command(kind, command):
            reasons.append(f"{LABELS[kind]}: показ и решение в одной команде — владелец не успел ответить")
            continue
        reply = last_human_input(event.get("transcript_path") or "", (lambda text, kind=kind: is_show_command(kind, text)) if kind in SHOW_CALLS else None)
        if reply is None:
            continue  # не видим реплику — не судим (fail-open)
        if reply.get("shown_index") is not None and reply["index"] < reply["shown_index"]:
            reasons.append(f"{LABELS[kind]}: показ был, а ответа владельца после него нет")
            continue
        ok, why = evidence(kind, options, focus(reply["text"]))
        if not ok:
            quoted = " ".join(reply["text"].split())[:120]
            reasons.append(f"{LABELS[kind]}: {why} (последний ответ: «{quoted}»)")
    if not reasons:
        return None
    return _deny(
        "negotiation-analysis: это решение владельца — спросите его (покажите, что именно будет сделано) и выполните "
        "после ответа. " + "; ".join(reasons) + "."
    )


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


def main() -> int:
    try:
        decision = decide(json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace")))
    except Exception:  # fail-open
        return 0
    if decision:
        sys.stdout.write(json.dumps(decision, ensure_ascii=True))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
