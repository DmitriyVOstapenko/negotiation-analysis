| Параметр | Значение |
|----------|----------|
| Версия | 0.1.0 |
| Статус | active |
| Owner | Дмитрий Остапенко |
| Создан | 2026-10-18 |
| Обновлён | 2026-10-18 |
| Назначение | Справочник движка `scripts/na.py`: все подкоманды и аргументы, раскладка рабочего каталога, формат `knowledge_proposal.json`, коды выхода, инварианты и опциональные возможности. Для навыков плагина и для разработчика. |

# Движок `scripts/na.py`

Единая точка входа детерминированной части системы. Только стандартная
библиотека Python 3.10+; сетевых вызовов нет; секреты читаются только из
переменных окружения (движку они не нужны). LLM-этапы (11 аналитических JSON,
финальный `report.md`, `knowledge_proposal.json`) движок не выполняет — он
регистрирует, проверяет, собирает, показывает diff и применяет только после
подтверждения пользователя.

```text
python3 <plugin>/scripts/na.py [--root R] [--json] <команда> [подкоманда] [параметры]
```

- `--root R` — рабочий каталог с данными (workspace). Если не задан, берётся
  переменная окружения `NA_WORKSPACE`, иначе текущий каталог. Флаг допустим как
  до, так и после подкоманды.
- `--json` — печатать результат машинно-читаемым JSON вместо короткой сводки.
- Код плагина и данные разделены: схемы — `<plugin>/core/contracts/`, шаблоны —
  `<plugin>/templates/`, всё остальное — в workspace.

## Раскладка рабочего каталога

```text
<root>/
├── profile/{role,organization,policy}.yaml     необязательный приватный профиль
├── negotiations/<meeting_id>/
│   ├── source_packet.json   transcript.txt   note.md (опц.)   meta.yaml   review.yaml
│   ├── analysis/01_extraction.json … 11_meeting_effectiveness.json
│   ├── analysis/validation_report.json   analysis/report_auto_validation.json
│   ├── analysis/audio_acoustic_metrics.json (опц.)
│   ├── report_auto.md   report.md
│   ├── follow_up.json   follow_up_protocol.md   follow_up_message.md
│   ├── knowledge_proposal.json   knowledge_diff.md   knowledge_apply.json
│   ├── run_manifest.json   analysis_bundle.json
├── knowledge/
│   ├── people/*.md   organizations/*.md (companies/ — устаревшее имя, читается)   topics/*.md   tactics_observed/*.md
│   ├── commitments/{open,closed}.jsonl   risks/{active,closed}.jsonl
│   ├── decisions/register.jsonl   assignments/register.jsonl
│   ├── review_decisions.jsonl   events/ledger.jsonl   index.md
└── system/                 производные, пересобираются целиком
    ├── control_center.{md,json}   control_center_validation.json
    ├── review_queue.{md,json}   search.sqlite   search_index_manifest.json
    ├── pipeline_run.json   event_projection/   event_projection_validation.json
```

Недостающие каталоги и пустые реестры создаются автоматически при первой
команде, которая в них нуждается.

## Подкоманды

### Источник

`source create --meeting <id> --transcript <file> [--note <file>] [--provider manual|plaud|wonderscribe|local_file|uploaded_transcript|document] [--captured-at ISO] [--title "..."] [--external-id ...] [--allow-duplicate]`

Создаёт `negotiations/<id>/`, копирует транскрипт в `transcript.txt` без
изменений, пишет `source_packet.json` (SourcePacket: `source_id`, `provider`,
`source_type`, `external_id_or_local_path`, `captured_at`, `ingested_at`,
`content_sha256`, `segments` с таймкодами `[HH:MM:SS]`/`HH:MM:SS` и метками
`Speaker N:` / `Имя:`, `speaker_labels`, `sensitivity: unknown`,
`processing_status: registered`, `consent`/`masking_mode`/`allowed_routes`/
`retention_decision: not_set`), а также скелеты `meta.yaml` и `review.yaml`.
Дедупликация: если встреча с таким же `content_sha256` уже есть — отказ (код 2)
с указанием существующего id; `--allow-duplicate` регистрирует с полем
`duplicate_of`.

`source gate --meeting <id> --consent yes|no --masking none|initials|pseudonyms|full --sensitivity public|internal|confidential|regulated --routes local_only|local_and_cloud_llm [--retain-source yes|no]`

Записывает решение шлюза в `review.yaml` (`processing_gate`, `source:
user_confirmation`) и `source_packet.json`. При `consent=yes` статус
`gate_passed`, иначе `blocked_by_gate`; без `yes` конвейер не считает встречу
готовой. Решение относится только к этой записи и не наследуется другими.

### Спикеры и ручная проверка

`speakers set --meeting <id> --map "Speaker 1=Иван Петров (CFO);Speaker 2=..."`

Карта спикеров в `review.yaml` (`speaker_mapping.status: confirmed`, `source:
user_confirmation`) и в `source_packet.json` (`speaker_map`). Роль в скобках
необязательна.

`review confirm --meeting <id> [--status confirmed|confirmed_with_open_items|pending]`
— фиксирует `review_status` (информационное поле).

### Проверка, отчёт, follow-up, манифест

`validate --meeting <id>` — 11 JSON по `core/contracts/contracts.schema.json`
(встроенный schema_runtime), буквальное присутствие каждой цитаты (`quote`,
`source_quote`, `related_quote`, `prior_position_quote`, `reaction_quote`,
`evidence_quote`) в `transcript.txt`, формат таймкодов, совпадение `meeting_id`
→ `analysis/validation_report.json`. Код 1 при ошибках. Непройденный шлюз и
отсутствие `report.md` — предупреждения.

`report --meeting <id>` — `report_auto.md`: блок «Один экран» (главный вывод,
требуемое решение, ключевые факты, риски, варианты, следующий шаг — из 01/07/08/
09/11) и затем полные разделы; `analysis/report_auto_validation.json`
(обязательные заголовки, участники, обязательства и риски встречи). Обязательства
и риски берутся из реестров knowledge/, а ещё не внесённые — из 07/08 с пометкой
«анализ (в базу не внесено)». Код 1, если проверка полноты не пройдена.

`followup --meeting <id>` — `follow_up.json`, `follow_up_protocol.md`,
`follow_up_message.md`; `delivery_status` всегда `draft_not_sent`, отправки нет.

`manifest --meeting <id>` — `run_manifest.json`: версии и хеши контрактов, хеши
входов (исходники, 11 JSON, предложение, реестры, файлы движка), этапы,
`overall_status`, `resume_from`, применённые предложения.

### База знаний

`knowledge diff --meeting <id>` — читает `negotiations/<id>/knowledge_proposal.json`,
сравнивает с текущим `knowledge/` и пишет `knowledge_diff.md` (unified diff для
Markdown, таблица для записей реестров). Ничего не применяет. Проверки:
пути только внутри `knowledge/` (кроме `review_decisions.jsonl` и `events/`),
`create` не поверх существующего файла, `update` только существующего,
сохранение всех строк с пометкой `(m)`, `append` только нового id, `update` не
переводит в терминальный статус (для этого `close`). Код 1 при ошибках
предусловий — применение заблокировано.

`knowledge apply --meeting <id> --confirm` — применяет предложение: пишет md,
добавляет/обновляет записи, при `close` переносит запись из `open`/`active` в
`closed` с полями `closed_at`, `closed_source: user_confirmation`,
`closed_in_meeting_id` (история не удаляется), дописывает события в
`knowledge/events/ledger.jsonl` по `knowledge_event.schema.json`, пишет квитанцию
`knowledge_apply.json`, отмечает diff как применённый и пересобирает
`knowledge/index.md` (если он создан движком). Требует показанного diff того же
предложения (по SHA-256). Без `--confirm` — отказ (код 2). Повторный apply того
же предложения ничего не дублирует (`status: already_applied`).

Формат `knowledge_proposal.json` (готовит LLM-этап):

```json
{
  "meeting_id": "...",
  "generated_at": "ISO-8601",
  "contract_version": "1.0.0",
  "files": [
    {"path": "knowledge/people/<slug>.md", "action": "create|update", "content": "<полное новое содержимое>"}
  ],
  "records": [
    {"registry": "knowledge/commitments/open.jsonl", "action": "append|update|close", "id": "<id>", "record": {...}}
  ]
}
```

- `files`: только Markdown внутри `knowledge/`; `organizations/` — основной
  каталог, существующий файл в `companies/` используется как текущее состояние,
  запись идёт в `organizations/`.
- `records.action`: `append` — новая запись (`record` целиком); `update` —
  слияние полей поверх существующей; `close` — для парных реестров перенос в
  закрытый с терминальным статусом (`done|cancelled` для обязательств,
  `closed|realized` для рисков; по умолчанию `done`/`closed`), для одиночных
  реестров (`decisions`, `assignments`) — смена статуса на терминальный.
- Пример — `templates/knowledge_proposal.template.json`.

### Очередь проверки

`review queue` — пересобирает `system/review_queue.{json,md}`: открытые пункты
`review.yaml`, непройденный шлюз, неподтверждённая карта спикеров, участники
`Speaker N`, выводы с `confidence possible|speculative`, обязательства без срока,
риски без владельца или срока.

`review record --item <rq-id> --decision <text> [--status closed|deferred]` —
дописывает решение в `knowledge/review_decisions.jsonl` с `source:
user_confirmation` и пересобирает очередь. Запись решения не меняет реестры:
изменение самой записи оформляется отдельным предложением.

### Конвейер и производные

`refresh --meeting <id> [--force]` — validate → report → followup → manifest для
одной встречи; при неизменных хешах входов готовая встреча переиспользуется.
Код 1 при сбое любого шага.

`refresh-all [--force]` — все встречи, очередь, синхронизация ledger (снимки
обязательств и рисков), проекция ledger и её сверка с реестрами, поисковый
индекс, `knowledge/index.md`, контрольный центр и его проверка →
`system/pipeline_run.json`. Код 0 только если все встречи `ready` и нет ошибок
целостности.

`status [--meeting <id>]` — состояние конвейера (этапы и `resume_from`) или
сводка по всем встречам; всегда код 0.

`control-center [--as-of YYYY-MM-DD]` — `system/control_center.md` и `.json`:
сводка, системные возможности, просрочки, риски высокой важности,
незавершённые встречи, открытые вопросы, реестры, целостность данных. Код 1 при
ошибках целостности.

`search "<запрос>" [--kind K] [--meeting ID] [--person ID] [--topic ID] [--limit N] [--any]`
— поиск по `system/search.sqlite` (FTS5; при его отсутствии в сборке SQLite —
резервный LIKE-поиск). Индекс строится автоматически, если его нет.

### Упаковка артефактов

`bundle pack --meeting <id>` — все `analysis/*.json` + `source_packet.json` +
`run_manifest.json` (если есть) → `negotiations/<id>/analysis_bundle.json`
вида `{"bundle_version", "contract_version", "meeting_id", "packed_at",
"files": {"analysis/01_extraction.json": {...}, ...}, "checksums": {...}}`.
Нужно для хранения встречи несколькими документами проекта вместо 13 файлов.

`bundle unpack --meeting <id> [--force]` — восстанавливает файлы из пакета.
Совпадающие файлы не трогаются; файл, изменённый позже `packed_at`, без
`--force` пропускается (код 1 и список пропущенных).

### Аудио (опционально)

`audio --check` — сообщает, какие зависимости отсутствуют (`numpy`, `soundfile`,
`librosa`); код 2, если слой недоступен. Остальные команды от них не зависят.

`audio --meeting <id> --wav <file> --segments <segments.json>` — инструментальные
метрики сегментов (`start_ms`, `end_ms`, `speaker`, `text`) →
`analysis/audio_acoustic_metrics.json`. Метрики не содержат смысловых или
клинических выводов и не меняют текстовый анализ.

## Коды выхода

| Код | Значение |
|---|---|
| 0 | успех |
| 1 | проверка не пройдена: схема, цитаты, полнота отчёта, ошибки предусловий diff, целостность, незавершённые этапы в `refresh-all`, пропуски в `bundle unpack` |
| 2 | ошибка предусловия или использования: нет файла или встречи, дубликат источника, `apply` без `--confirm` или без показанного diff, неизвестный пункт очереди, недоступный аудиослой |

С `--json` ошибка печатается как `{"valid": false, "error": "...", "exit_code": N}`.

## Инварианты

- Обязательство, риск, решение и пункт очереди не закрываются без решения
  пользователя: закрытие — только через `knowledge apply --confirm` с `close`
  или `review record`.
- Строки с пометкой `(m)` в файлах `knowledge/` обязаны сохраниться в
  предложении, иначе diff и apply блокируются.
- `knowledge/events/ledger.jsonl` — только дописывание; проекция
  `system/event_projection/` сверяется с реестрами при каждом `refresh-all`.
- Всё в `system/` производно и пересобирается; `knowledge/index.md`
  пересобирается только если создан движком (есть маркер `generated by na.py`).
- Ошибка схемы или цитаты блокирует `ready`; follow-up всегда `draft_not_sent`.
- Движок не ходит в сеть и не читает секреты из файлов проекта.

## Совместимость с прежним рабочим каталогом

Читаются устаревшие поля: `personal_data.processing_consent: true` в
`review.yaml` считается `consent=yes`; `note_plaud.md`/`source_note.md`
учитываются в хешах входов; `knowledge/companies/` читается как организации.
Встреча без `source_packet.json` проверяется с предупреждением.

## Тесты

`python3 tests/run_tests.py` — сквозной путь на временном workspace с
синтетической встречей `tests/fixtures/synthetic-meeting/` (вымышленные
компании и люди).
