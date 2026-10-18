# Контур перевода

Как устроен перевод и как его продолжать. Единицы перевода и статусы — по
спецификации `TRANSLATION_SPEC` из пакета передачи (принципы: не добавлять идей, не
смягчать, различать речь автора и участников, термины по словарю, сомнения — в заметки,
машинный перевод не выдавать за проверенный).

## Канон

1. `python3 tools/canon_tools.py split` — `canon-src/*.md` → `canon-units/<файл>.src.json`
   (преамбула + 275 единиц `###` с SHA-256) и `titles.src.json`.
2. Перевод заголовков → `titles.ru.json` (единая карта для `[[ссылок]]`).
3. Перевод единиц → `canon-units/<файл>.ru.parts/` → `python3 tools/canon_tools.py merge-parts <файл>`
   → `canon-units/<файл>.ru.json`.
4. `python3 tools/canon_tools.py assemble` — сборка `skills/negociar-ru/data/canon/<файл>`:
   заголовки `### Русский / English`, ссылки `[[…]]` переведены по карте.
5. `python3 tools/canon_tools.py check` — 275 принципов, ссылки разрешаются, остатки
   испанского, хеши → `canon-status.json`.

## Случаи

1. `python3 tools/cases_tools.py titles-export` → перевод → `titles-import` (1019 заголовков).
2. `python3 tools/cases_tools.py export --size 25` — пакеты `cases/batch-NNN.src.json`.
3. Перевод пакета по `AGENT-BRIEF-cases.md` → `cases/work/batch-NNN/<id>-<n>.json` →
   `python3 tools/cases_tools.py pack batch-NNN` (проверка id, непустых полей, длины ≥ 40 %
   исходника, остатков испанского, хешей) → `cases/accepted/batch-NNN.ru.json`.
4. `python3 tools/cases_tools.py assemble` — `skills/negociar-ru/data/casos.json`: у принятых
   случаев `title/body/domain` русские (`title_es` сохранён), у остальных — заголовок
   (`title_only`) или исходник (`untranslated`); `translation_status` в каждой записи.
5. `python3 tools/cases_tools.py status` → `cases-status.json`.

## Статусы

`machine_draft` → `terminology_checked` → `reviewed` → `approved`; `blocked` — смысловая
неоднозначность. Статус корпуса = минимальный статус обязательных элементов.

## Заметки проверки

`translation-review.jsonl` — по строке на сомнение: цитата, в чём вопрос, предложение, статус.
