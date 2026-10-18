---
id: "[person-id]--[tactic_type]"
person_id: "[person-id]"
tactic_type: "[tactic_type из контракта 02_tactics.json или 03_manipulations.json]"
description: "[одно предложение: какая тактика у какого участника наблюдается и с какой уверенностью]"
observations_count: 0
created_at: "[ISO-8601]"
updated_at: "[ISO-8601]"
---

# [tactic_type] у [Имя]

## Наблюдения

Каждое наблюдение — с ссылкой на встречу, таймкодом, цитатой, эффектом и confidence.

- **[YYYY-MM-DD]** ([[meeting-id]] `~HH:MM:SS`): «[цитата из транскрипта]». Эффект: [worked|partial|failed|neutral|unclear]. Контр-ход: [что было сделано или «не было»]. Confidence: [certain|probable|possible|speculative].

## Паттерн

Формулируется только при трёх и более наблюдениях с confidence не ниже probable. До этого — «единичное наблюдение».

## Что работает против

- [Проверенный контр-ход с указанием встречи, где он сработал]

## Ручные заметки

<!-- Строки с пометкой (m) не перезаписываются автоматически. -->
