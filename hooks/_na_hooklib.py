"""Общие помощники хуков negotiation-analysis: цели записи в команде оболочки, пути из заплатки
apply_patch (Codex) и последняя реплика человека из транскрипта сессии.

Только стандартная библиотека; сети нет. Любая ошибка разбора — пустой результат, решение
остаётся за вызывающим хуком (он пропускает: fail-open).

Происхождение. Часть идей и кода взята из проекта ivan (https://github.com/Coman-os/ivan,
каталог cowork/hooks) и переработана:

- PATCH_FILE_RE, PATCH_MOVE_RE, patch_targets() — по _host_adapter.py (_FILE_HEADER, _MOVE_TO,
  _paths_from_patch): грамматика заголовков заплатки Codex;
- iter_transcript_messages() — по _transcript_io.py (load_messages): чтение transcript_path
  построчно, формы записей Claude Code и Codex (rollout);
- FALLBACK_WRITE_RE — по post_bash_write_relay.py (WRITE_RE, строки 48–54): резервные
  регулярные выражения целей записи; расширены на любые расширения файлов, tee, rm/unlink и
  перенаправление с дескриптором.

Для этих частей действует уведомление правообладателя:

    Copyright (c) 2026 Maxim Opilkin

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

Копия лицензии — licenses/third-party/ivan-MIT.txt (см. также docs/IVAN.md). Остальной код файла — © 2026 Дмитрий Остапенко, MIT
(LICENSE репозитория).
"""

from __future__ import annotations

import json
import os
import re
import shlex

# ---------------------------------------------------------------------------
# Команда оболочки → цели записи
# ---------------------------------------------------------------------------

SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;", ";&", ";;&"}
REDIRECT_WRITE = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}
REDIRECT_READ = {"<", "<<", "<<<", "<<-", "<&"}
WRAPPERS = {"sudo", "env", "nohup", "time", "command", "exec", "nice", "stdbuf", "timeout", "builtin"}
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
VAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Кто пишет в файл: kind — write (создать или перезаписать), modify (изменить на месте),
# delete (удалить или унести).
PY_OPEN = re.compile(r"""\bopen\(\s*[rbuf]*(['"])(?P<p>[^'"\n]+)\1\s*,\s*(?:mode\s*=\s*)?[rbuf]*(['"])(?P<m>[^'"\n]*)\3""")
PY_VAR_OPEN = re.compile(r"""\bopen\(\s*(?P<v>[A-Za-z_]\w*)\s*,\s*(?:mode\s*=\s*)?[rbuf]*(['"])(?P<m>[^'"\n]*)\2""")
PY_PATH_CALL = re.compile(r"""\bPath\(\s*[rbuf]*(['"])(?P<p>[^'"\n]+)\1\s*\)\s*\.\s*(?P<f>write_text|write_bytes|unlink|rename|replace|touch|rmdir|open)\b(?P<rest>\([^)\n]*\))?""")
PY_VAR_CALL = re.compile(r"""\b(?:Path\(\s*)?(?P<v>[A-Za-z_]\w*)\s*\)?\s*\.\s*(?P<f>write_text|write_bytes|unlink|touch)\(""")
PY_TWO_ARGS = re.compile(r"""\b(?P<f>shutil\.(?:copy|copy2|copyfile|copytree|move)|os\.(?:rename|replace))\(\s*[rbuf]*(['"])(?P<a>[^'"\n]+)\2\s*,\s*[rbuf]*(['"])(?P<b>[^'"\n]+)\4""")
PY_ONE_ARG = re.compile(r"""\b(?:os\.(?:remove|unlink|rmdir)|shutil\.rmtree)\(\s*[rbuf]*(['"])(?P<p>[^'"\n]+)\1""")
PY_ASSIGN = re.compile(r"""(?m)(?:^|;)\s*(?P<v>[A-Za-z_]\w*)\s*=\s*(?:Path\(\s*)?[rbuf]*(['"])(?P<p>[^'"\n]+)\2""")
NODE_CALL = re.compile(r"""\.(?:promises\.)?(?P<f>writeFileSync|appendFileSync|writeFile|appendFile|rmSync|unlinkSync|unlink|rm|renameSync|rename|copyFileSync|copyFile|cpSync)\(\s*(['"`])(?P<p>[^'"`\n]+)\2(?:\s*,\s*(['"`])(?P<q>[^'"`\n]+)\4)?""")

FALLBACK_WRITE_RE = [
    (re.compile(r"(?<![<>&])\d?>>?\|?\s*(['\"]?)(?P<p>[^\s|&;<>()'\"]+)\1"), "write"),
    (re.compile(r"\btee\s+(?:-\w+\s+)*(['\"]?)(?P<p>[^\s|&;<>()'\"]+)\1"), "write"),
    (re.compile(r"\b(?:sed|perl)\s+-\w*i\b[^|;&\n]*?\s(['\"]?)(?P<p>[^\s|&;<>()'\"]+)\1\s*(?:$|[|;&\n])"), "modify"),
    (re.compile(r"\b(?:io\.)?open\(\s*['\"](?P<p>[^'\"]+)['\"]\s*,\s*['\"][wax]"), "write"),
    (re.compile(r"\b(?:cp|mv|install)\s+[^|;&\n]*?\s(['\"]?)(?P<p>[^\s|&;<>()'\"]+)\1\s*(?:$|[|;&\n])"), "write"),
    (re.compile(r"\b(?:rm|unlink|shred)\s+(?:-\w+\s+)*(['\"]?)(?P<p>[^\s|&;<>()'\"]+)\1"), "delete"),
]

PS_WRITE = {"set-content": "write", "add-content": "write", "out-file": "write", "new-item": "write", "clear-content": "modify",
            "remove-item": "delete", "del": "delete", "erase": "delete", "ri": "delete", "copy-item": "copy", "move-item": "move"}
PS_PATH_OPTS = {"-path", "-literalpath", "-filepath", "-destination"}
PS_SWITCHES = {"-force", "-recurse", "-nonewline", "-append", "-whatif", "-confirm", "-passthru", "-noclobber"}


def split_heredocs(command: str) -> tuple[str, list[str]]:
    """Убрать тела heredoc (они не часть синтаксиса команды); вернуть (команда, тела)."""
    lines = command.split("\n")
    kept: list[str] = []
    bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for _, delimiter in HEREDOC_RE.findall(line):
            body: list[str] = []
            while index < len(lines) and lines[index].strip() != delimiter:
                body.append(lines[index])
                index += 1
            index += 1
            bodies.append("\n".join(body))
    return "\n".join(kept), bodies


def _newlines_to_separators(text: str) -> str:
    out: list[str] = []
    quote = None
    escape = False
    for char in text:
        if escape:
            out.append(char)
            escape = False
            continue
        if char == "\\" and quote != "'":
            escape = True
            out.append(char)
            continue
        if quote:
            if char == quote:
                quote = None
            out.append(char)
            continue
        if char in "'\"":
            quote = char
        out.append(" ; " if char == "\n" else char)
    return "".join(out)


def tokenize(text: str) -> list[str]:
    lexer = shlex.shlex(_newlines_to_separators(text), posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    return list(lexer)


def _simple_commands(tokens: list[str]) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            commands.append([])
        else:
            commands[-1].append(token)
    return [command for command in commands if command]


def _expand(token: str, variables: dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        if name in variables:
            return variables[name]
        return os.environ.get(name, match.group(0))

    return os.path.expanduser(VAR_RE.sub(repl, token))


def _non_options(args: list[str]) -> list[str]:
    result: list[str] = []
    after_double_dash = False
    for arg in args:
        if after_double_dash or not arg.startswith("-") or arg == "-":
            result.append(arg)
        elif arg == "--":
            after_double_dash = True
    return result


def python_targets(code: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    variables = {match.group("v"): match.group("p") for match in PY_ASSIGN.finditer(code)}
    for match in PY_OPEN.finditer(code):
        if set(match.group("m")) & set("wax+"):
            found.append((match.group("p"), "write"))
    for match in PY_VAR_OPEN.finditer(code):
        if match.group("v") in variables and set(match.group("m")) & set("wax+"):
            found.append((variables[match.group("v")], "write"))
    for match in PY_PATH_CALL.finditer(code):
        function = match.group("f")
        if function == "open" and not set(match.group("rest") or "") & set("wax+"):
            continue
        found.append((match.group("p"), "delete" if function in ("unlink", "rename", "replace", "rmdir") else "write"))
    for match in PY_VAR_CALL.finditer(code):
        if match.group("v") in variables:
            found.append((variables[match.group("v")], "delete" if match.group("f") == "unlink" else "write"))
    for match in PY_TWO_ARGS.finditer(code):
        found.append((match.group("b"), "write"))
        if match.group("f") in ("shutil.move", "os.rename", "os.replace"):
            found.append((match.group("a"), "delete"))
    for match in PY_ONE_ARG.finditer(code):
        found.append((match.group("p"), "delete"))
    return found


def node_targets(code: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in NODE_CALL.finditer(code):
        function = match.group("f")
        if function.startswith(("rename", "copy", "cp")) and match.group("q"):
            found.append((match.group("q"), "write"))
            if function.startswith("rename"):
                found.append((match.group("p"), "delete"))
        elif function.startswith(("rm", "unlink")):
            found.append((match.group("p"), "delete"))
        else:
            found.append((match.group("p"), "write"))
    return found


def _strip_wrappers(argv: list[str]) -> list[str]:
    while argv:
        head = os.path.basename(argv[0])
        if head not in WRAPPERS:
            break
        argv = argv[1:]
        if head == "timeout" and argv and not argv[0].startswith("-"):
            argv = argv[1:]
        while argv and (argv[0].startswith("-") or (head == "env" and ASSIGN_RE.match(argv[0]))):
            argv = argv[1:]
    return argv


def is_na_py(token: str) -> bool:
    return os.path.basename(token.replace("\\", "/")) == "na.py"


def na_invocations(command: str) -> list[list[str]]:
    """Аргументы каждого вызова na.py в команде (после имени скрипта)."""
    stripped, _ = split_heredocs(command)
    try:
        commands = _simple_commands(tokenize(stripped))
    except ValueError:
        return [arguments.split() for arguments in re.findall(r"na\.py['\"]?\s+([^|;&\n]*)", stripped)]
    calls: list[list[str]] = []
    variables: dict[str, str] = {}
    for argv in commands:
        if argv and argv[0] == "export":
            argv = argv[1:]
        while argv and ASSIGN_RE.match(argv[0]):
            name, value = argv.pop(0).split("=", 1)
            variables[name] = value
        argv = _strip_wrappers(argv)
        if argv and VAR_RE.fullmatch(argv[0]):
            match = VAR_RE.fullmatch(argv[0])
            value = variables.get(match.group(1) or match.group(2), "")
            try:
                argv = shlex.split(value) + argv[1:]
            except ValueError:
                argv = value.split() + argv[1:]
        for position, token in enumerate(argv[:3]):
            if is_na_py(token):
                calls.append(argv[position + 1 :])
                break
    # Пути Windows без кавычек (C:\p\na.py) разбор по правилам POSIX съедает: обратная косая там —
    # экранирование. Запасной поиск по сырому тексту не даёт такому вызову пройти незамеченным.
    raw_calls = [arguments.split() for arguments in re.findall(r"na\.py['\"]?\s+([^|;&\n]*)", stripped, flags=re.I)]
    if len(raw_calls) > len(calls):
        return raw_calls
    return calls


def shell_targets(command: str, cwd: str, powershell: bool = False) -> list[tuple[str, str]]:
    """(абсолютный путь, kind) для каждой цели записи в команде; эвристика, без исполнения.

    powershell — команда PowerShell: обратная косая там разделитель пути, а не экранирование.
    """
    if powershell:
        command = command.replace("\\", "/")
    stripped, bodies = split_heredocs(command)
    try:
        commands = _simple_commands(tokenize(stripped))
    except ValueError:
        return _fallback_targets(stripped, bodies, cwd)
    variables: dict[str, str] = {}
    found: list[tuple[str, str]] = []
    current = cwd

    def add(path: str, kind: str, base: str) -> None:
        path = _expand(path, variables)
        if not path or path.startswith("/dev/") or path == "-":
            return
        absolute = path if os.path.isabs(path) else os.path.join(base, path)
        found.append((os.path.normpath(absolute), kind))

    for raw in commands:
        argv: list[str] = []
        skip_next = False
        for position, token in enumerate(raw):
            if skip_next:
                skip_next = False
                continue
            following = raw[position + 1] if position + 1 < len(raw) else None
            if token in REDIRECT_WRITE:
                if following is not None and not (token == ">&" and (following.isdigit() or following == "-")):
                    add(following, "write", current)
                skip_next = True
                continue
            if token in REDIRECT_READ:
                skip_next = True
                continue
            if token.isdigit() and following in REDIRECT_WRITE | REDIRECT_READ:
                continue
            argv.append(token)
        assignments = []
        while argv and ASSIGN_RE.match(argv[0]):
            assignments.append(argv.pop(0))
        for assignment in assignments:
            name, value = assignment.split("=", 1)
            variables[name] = _expand(value, variables)
        argv = _strip_wrappers(argv)
        if not argv:
            continue
        name = os.path.basename(argv[0].replace("\\", "/")).lower()
        args = argv[1:]
        if name == "export":
            for item in args:
                if ASSIGN_RE.match(item):
                    key, value = item.split("=", 1)
                    variables[key] = _expand(value, variables)
            continue
        if name in ("cd", "pushd"):
            target = _expand(args[0], variables) if args else os.path.expanduser("~")
            current = os.path.normpath(target if os.path.isabs(target) else os.path.join(current, target))
            continue
        plain = _non_options(args)
        if name == "tee":
            for item in plain:
                add(item, "write", current)
        elif name in ("sed", "gsed", "perl"):
            in_place = any(arg.startswith("--in-place") or (arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]) for arg in args)
            if in_place:
                for item in plain:
                    add(item, "modify", current)
        elif name in ("cp", "install", "rsync", "ln", "mv"):
            target_dir = None
            for position, arg in enumerate(args):
                if arg in ("-t", "--target-directory") and position + 1 < len(args):
                    target_dir = args[position + 1]
                elif arg.startswith("--target-directory="):
                    target_dir = arg.split("=", 1)[1]
            sources = [item for item in plain if item != target_dir]
            if target_dir is None and len(sources) >= 2:
                target_dir = sources.pop()
            if target_dir is None:
                continue
            add(target_dir, "write", current)
            destination = _expand(target_dir, variables)
            destination_abs = destination if os.path.isabs(destination) else os.path.join(current, destination)
            into_dir = target_dir.endswith(("/", "\\")) or os.path.isdir(destination_abs) or len(sources) > 1
            if into_dir:
                for source in sources:
                    add(os.path.join(destination_abs, os.path.basename(_expand(source, variables).rstrip("/\\"))), "write", current)
            if name == "mv":
                for source in sources:
                    add(source, "delete", current)
        elif name in ("rm", "unlink", "shred", "rmdir"):
            for item in plain:
                add(item, "delete", current)
        elif name == "truncate":
            skip = False
            for arg in args:
                if skip:
                    skip = False
                    continue
                if arg in ("-s", "--size", "-r", "--reference"):
                    skip = True
                    continue
                if not arg.startswith("-"):
                    add(arg, "modify", current)
        elif name == "touch":
            for item in plain:
                add(item, "write", current)
        elif name == "dd":
            for arg in args:
                if arg.startswith("of="):
                    add(arg[3:], "write", current)
        elif re.fullmatch(r"(?:python|py)(?:\d+(?:\.\d+)?)?(?:\.exe)?", name):
            if args and is_na_py(args[0]):
                continue  # законный путь записи в базу — скрипт na.py
            code = None
            if "-c" in args and args.index("-c") + 1 < len(args):
                code = args[args.index("-c") + 1]
            elif "-m" not in args and (not plain or plain[0] == "-"):
                code = "\n".join(bodies)
            if code:
                for path, kind in python_targets(code):
                    add(path, kind, current)
        elif name in ("node", "node.exe"):
            for flag in ("-e", "--eval", "-p"):
                if flag in args and args.index(flag) + 1 < len(args):
                    for path, kind in node_targets(args[args.index(flag) + 1]):
                        add(path, kind, current)
        elif name in PS_WRITE:
            kind = PS_WRITE[name]
            values: list[str] = []
            take_next = False
            skip_next = False
            for arg in args:
                lowered = arg.lower()
                if take_next:
                    values.append(arg)
                    take_next = False
                    continue
                if skip_next:
                    skip_next = False
                    continue
                if lowered in PS_PATH_OPTS:
                    take_next = True
                elif lowered.startswith("-"):
                    skip_next = lowered not in PS_SWITCHES
                else:
                    values.append(arg)
            if kind in ("copy", "move"):
                if values:
                    add(values[-1], "write", current)
                if kind == "move":
                    for value in values[:-1]:
                        add(value, "delete", current)
            else:
                for value in values:
                    add(value, kind, current)
    return found


def _fallback_targets(stripped: str, bodies: list[str], cwd: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for pattern, kind in FALLBACK_WRITE_RE:
        for match in pattern.finditer(stripped):
            path = os.path.expanduser(match.group("p"))
            if path.startswith("/dev/") or path.startswith("&"):
                continue
            found.append((os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path)), kind))
    if re.search(r"\bpython|\bpy\b", stripped):
        for code in [stripped, *bodies]:
            for path, kind in python_targets(code):
                found.append((os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path)), kind))
    return found


# ---------------------------------------------------------------------------
# Заплатка apply_patch (Codex)
# ---------------------------------------------------------------------------

PATCH_FILE_RE = re.compile(r"^\s*\*\*\* (Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
PATCH_MOVE_RE = re.compile(r"^\s*\*\*\* Move to: (.+?)\s*$", re.MULTILINE)
PATCH_KIND = {"Add": "write", "Update": "modify", "Delete": "delete"}


def patch_targets(patch: str, cwd: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    moved = bool(PATCH_MOVE_RE.search(patch))
    for operation, path in PATCH_FILE_RE.findall(patch):
        kind = "delete" if (operation == "Update" and moved) else PATCH_KIND[operation]
        found.append((path, kind))
    found += [(path, "write") for path in PATCH_MOVE_RE.findall(patch)]
    return [(os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path)), kind) for path, kind in found]


def looks_like_patch(text: str) -> bool:
    return "*** Begin Patch" in text or bool(PATCH_FILE_RE.search(text))


# ---------------------------------------------------------------------------
# Транскрипт → последняя реплика человека
# ---------------------------------------------------------------------------

HARNESS_PREFIXES = ("<system-reminder>", "<task-notification>", "<command-", "<local-command", "caveat:", "this session is being continued", "<ide_", "[request interrupted")
ANSWER_RE = re.compile(r'"(?:[^"\\]|\\.)*"\s*=\s*"((?:[^"\\]|\\.)*)"')


def _human_text(content: object) -> str | None:
    texts: list[str] = []
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("text", "input_text") and isinstance(block.get("text"), str):
                texts.append(block["text"])
    kept = [text for text in texts if text.strip() and not text.strip().lower().startswith(HARNESS_PREFIXES)]
    return "\n".join(kept).strip() or None


def _answer_text(entry: dict, block: dict) -> str:
    result = entry.get("toolUseResult")
    if isinstance(result, dict) and isinstance(result.get("answers"), dict):
        return " ; ".join(str(value) for value in result["answers"].values())
    content = block.get("content")
    if isinstance(content, list):
        content = " ".join(item.get("text", "") for item in content if isinstance(item, dict))
    if not isinstance(content, str):
        return ""
    answers = ANSWER_RE.findall(content)
    return " ; ".join(answers)


def iter_transcript_messages(path: str):
    """(номер, запись, сообщение) по транскрипту: формы Claude Code и Codex (rollout)."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if isinstance(message, dict) and message.get("role"):
                yield number, entry, message
                continue
            payload = entry.get("payload")
            if isinstance(payload, dict) and payload.get("role") and payload.get("type", "message") == "message":
                yield number, entry, {"role": payload["role"], "content": payload.get("content")}
                continue
            if entry.get("role"):
                yield number, entry, entry


def last_human_input(transcript_path: str, is_show=None) -> dict | None:
    """Последний ввод человека: текст реплики или ответ на AskUserQuestion.

    Возвращает {"text", "index", "source", "shown_index"} либо None, если транскрипт не читается,
    человеческой реплики нет или вопрос AskUserQuestion ещё ждёт ответа. shown_index — номер
    последнего вызова команды оболочки, для текста которой is_show(command) истинно (показ перед
    решением), или None.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    last: dict | None = None
    asked: dict[str, int] = {}
    answered: set[str] = set()
    shown_index = None
    for number, entry, message in iter_transcript_messages(transcript_path):
        if entry.get("isMeta") or entry.get("isSidechain") or entry.get("isCompactSummary") or entry.get("isVisibleInTranscriptOnly"):
            continue
        role = message.get("role")
        content = message.get("content")
        if role == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") == "AskUserQuestion" and block.get("id"):
                    asked[block["id"]] = number
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                command = tool_input.get("command")
                if is_show is not None and isinstance(command, str) and is_show(command):
                    shown_index = number
        elif role == "user":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in asked:
                        answered.add(block["tool_use_id"])
                        text = _answer_text(entry, block)
                        if text:
                            last = {"text": text, "index": number, "source": "AskUserQuestion"}
            text = _human_text(content)
            if text:
                last = {"text": text, "index": number, "source": "message"}
    if any(tool_id not in answered for tool_id in asked if last is None or asked[tool_id] > last["index"]):
        return None  # вопрос задан, ответ ещё не записан
    if last is not None:
        last["shown_index"] = shown_index
    return last
