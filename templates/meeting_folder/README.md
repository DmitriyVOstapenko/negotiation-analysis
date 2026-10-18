---
description: "Состав папки встречи negotiations/<meeting_id>/: какие файлы создаёт движок, какие пишет LLM-этап, какие — человек."
---

# Папка встречи

Папку создаёт `na.py source create`; вручную копировать шаблон не нужно.
Идентификатор рекомендуется строить как `YYYY-MM-DD-short-title` (латиница,
цифры, дефисы). Одна и та же запись не регистрируется дважды: движок сравнивает
SHA-256 транскрипта с уже зарегистрированными встречами.

```text
negotiations/<meeting_id>/
├── source_packet.json          SourcePacket: происхождение, хеш, сегменты, шлюз   (движок)
├── transcript.txt              канонический транскрипт — единственный источник цитат (копия без изменений)
├── note.md                     необязательное резюме провайдера; не заменяет транскрипт
├── meta.yaml                   метаданные встречи                                  (движок + человек)
├── review.yaml                 шлюз согласия, карта спикеров, границы анализа      (решения пользователя)
├── analysis/
│   ├── 01_extraction.json … 11_meeting_effectiveness.json   11 артефактов         (LLM-этап)
│   ├── validation_report.json           схема + буквальные цитаты                (na.py validate)
│   ├── report_auto_validation.json      полнота автоотчёта                       (na.py report)
│   └── audio_acoustic_metrics.json      опциональный акустический слой           (na.py audio)
├── report_auto.md              детерминированный отчёт с блоком «Один экран»      (na.py report)
├── report.md                   финальная редакция                                  (человек / LLM)
├── follow_up.json, follow_up_protocol.md, follow_up_message.md   всегда draft_not_sent (na.py followup)
├── knowledge_proposal.json     предложение по базе знаний                          (LLM-этап)
├── knowledge_diff.md           показанный diff; после apply — отметка о применении (na.py knowledge diff/apply)
├── knowledge_apply.json        квитанция применения с хешами предложений           (na.py knowledge apply)
├── run_manifest.json           версии контрактов, хеши входов, этапы, resume_from  (na.py manifest / refresh)
└── analysis_bundle.json        упаковка JSON-артефактов в один файл                (na.py bundle pack)
```

Аудио в папке не хранится по умолчанию: его судьба определяется решением
`retention_decision` в шлюзе согласия.
