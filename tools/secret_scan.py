#!/usr/bin/env python3
"""secret_scan.py — сканер секретов для файлов репозитория (deny-list).

Происхождение: hooks/secret-scan.py из плагина ivan (github.com/Coman-os/ivan).
Copyright (c) 2026 Maxim Opilkin. MIT License — полный текст: licenses/third-party/ivan-MIT.txt.
Изменения negotiation-analysis (2026-10-19): добавлены шаблоны ключа бота Telegram и ключа OpenAI,
убран шаблон Яндекс OAuth, русские сообщения под наш репозиторий, выход 0 без файлов.

Вызов:
  - перед коммитом (.git/hooks/pre-commit):  python3 tools/secret_scan.py --staged
  - вручную / в CI:                         python3 tools/secret_scan.py --all | <файлы…>

Возврат: 0 — чисто; 1 — найдены секреты (печатает файл:строка и имя шаблона, значение скрыто);
2 — ошибка вызова.
"""

import re
import subprocess
import sys

# Имя шаблона → регулярное выражение. Пороги длины не ловят заглушки из документации.
PATTERNS = {
    "telegram-bot-token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "bearer-header": re.compile(r"Bearer\s+[A-Za-z0-9._-]{30,}"),
    "github-pat": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "github-fine-grained": re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
    "aws-akid": re.compile(r"AKIA[0-9A-Z]{16}"),
    "anthropic-key": re.compile(r"sk-ant-[A-Za-z0-9_-]{30,}"),
    "openai-key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{40,}"),
    "pem-private-key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
}

# Строки, которые заведомо не секрет (описание самого сканера, заглушки).
ALLOW_MARKERS = ("REDACTED", "deny-list", "<REDACTED", "PATTERNS =", "re.compile(", "secret-scan: allow")


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"], capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f.strip()]


def all_tracked():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f.strip()]


def mask(value):
    return f"{value[:6]}…[{len(value)} символов, скрыто]"


def staged_content(path):
    """Содержимое так, как оно уйдёт в коммит (из индекса), а не с диска."""
    out = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return out.stdout


def scan(path, from_index=False):
    hits = []
    try:
        if from_index:
            content = staged_content(path)
            if content is None:
                return hits
            lines = content.splitlines(keepends=True)
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        for lineno, line in enumerate(lines, 1):
            if any(marker in line for marker in ALLOW_MARKERS):
                continue
            for name, rx in PATTERNS.items():
                match = rx.search(line)
                if match:
                    hits.append((path, lineno, name, mask(match.group(0))))
    except (OSError, UnicodeError):
        pass
    return hits


def main(argv):
    args = argv[1:]
    from_index = "--staged" in args
    if from_index:
        files = staged_files()
    elif "--all" in args:
        files = all_tracked()
    elif args:
        files = args
    else:
        print("Использование: secret_scan.py [--staged | --all | <файл>...]", file=sys.stderr)
        return 2
    hits = []
    for name in files:
        hits.extend(scan(name, from_index=from_index))
    if hits:
        print("secret-scan: найдены возможные секреты — коммит остановлен:\n")
        for path, lineno, name, masked in hits:
            print(f"  {path}:{lineno}  [{name}]  {masked}")
        print("\nУберите секрет из файла: ключи — только в переменных окружения или в delivery/secrets/ рабочей папки, вне репозитория.")
        print("Ложное срабатывание — добавьте в строку пометку «secret-scan: allow».")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
