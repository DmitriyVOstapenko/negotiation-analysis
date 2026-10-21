| Параметр | Значение |
|----------|----------|
| Версия | 0.1.0 |
| Статус | active |
| Owner | Дмитрий Остапенко |
| Создан | 2026-10-18 |
| Обновлён | 2026-10-21 |
| Назначение | Краткая карта 11 контрактных артефактов анализа встречи: какой файл, какие обязательные поля и enum, откуда берётся содержание. Точный источник истины — `core/contracts/contracts.schema.json`; при расхождении применять схему. |

# Карта контрактов анализа (contract version 1.1.0)

Общие типы: `confidence` ∈ `certain | probable | possible | speculative`;
`evidence` = `{quote, timestamp}`; `timestamp` — `HH:MM:SS` (или `MM:SS` как в транскрипте);
`nullable_*` — значение или `null`. Во всех файлах `meeting_id` = каталог встречи.
Идентификаторы: `person_id` — slug латиницей (`ivan-petrov-primer-holding`), `company_id` —
slug организации, `topic_id` — slug темы; те же id используются в `knowledge/`.
Поля везде перечислены полностью: лишнее поле — ошибка схемы. Поле с пометкой
«необязательно» можно не писать; остальные обязательны (значение `null` — там, где оно
допустимо).

Необязательные поля `participant.evidence`, `participant.evidence_note`,
`excluded_source_range.to`, `concession.prior_position_speaker` и
`commitment_item.related_mentions` добавлены в версии 1.1.0 (2026-10-21): прежние
разборы (1.0.0) остаются валидными.

## Восемь основных артефактов (из транскрипта)

| Файл | Метод (промпт) | Обязательные поля верхнего уровня |
|---|---|---|
| `01_extraction.json` | B.1 + общая экстракция | `participants[]`, `companies[]`, `topics[]` (все три — минимум один элемент), `key_facts[]`, `outcome` ∈ agreed/partial/deferred/failed/no_decision, `outcome_summary`, `excluded_source_range` (объект `{from, to, reason}` или null; `to` необязательно) |
| `02_tactics.json` | B.3, B.4 | `cialdini[]`, `voss[]`, `debug_possible[]` (слабые сигналы confidence ≤ possible; форма элемента свободная) |
| `03_manipulations.json` | B.5 | `manipulations[]`, `conclusion` |
| `04_arguments.json` | B.6 | `arguments[]` |
| `05_power.json` | B.7 | `talk_time_share[]`, `question_marks_in_transcript[]`, `interruptions[]`, `dominant_speaker`, `framing_control`, `framing_moments[]`, `concessions[]`, `evidence_note` |
| `06_emotions.json` | B.8 | `source` ∈ text_only/audio/both, `sentiment_points[]`, `overall_summary{overall, most_negative_timestamp, most_tense_timestamp, general_trend}`, `limitations` |
| `07_commitments.json` | B.2 | `commitments[]`, `open_questions[]` |
| `08_risks.json` | из 01–07 | `risks[]` |

### Элементы

**participant**: `person_id, full_name, role` (enum: owner, founder, owner_founder, CEO, board_chair, board_member, CFO, COO, CTO_CIO, CSO, business_unit_head, functional_head, treasury_head, budgeting_head, accounting_head, procurement_head, legal_head, sales_director, key_account_manager, project_manager, government_customer_representative, government_official, regulator_representative, lawyer, engineer, external_advisor, other), `role_freeform` (должность словами | null), `company_id, is_our_side, identity_source, confidence`; необязательно `evidence[]`, `evidence_note`.
- `identity_source` — откуда подтверждена личность: `transcript` — из самой записи;
  `user_confirmation` — подтвердил пользователь (`user_confirmed` — равнозначное прежнее
  значение; в новых разборах писать `user_confirmation`, как в `review.yaml` и `*_source` 09);
  `existing_knowledge` — по карточке базы (`knowledge/people/`).
- Способ установления в `identity_source` не входит. Он пишется в `evidence` — реплики
  самопредставления или обращения по имени (`{quote, timestamp}`; цитаты проверяются по
  транскрипту без атрибуции: по имени обращается другой человек) — и в `evidence_note` словами:
  «представился сам», «обратились по имени», «вывод по содержанию: …» (тогда `confidence` не
  выше `probable`), «подтвердил пользователь». Те же доказательства по меткам `Speaker N` —
  в предложении карты спикеров (B.1).

**company**: `id, legal_name` (null если не назван), `short_name, inn` (null если не назван), `is_our_side, relation_type` ∈ customer/supplier/partner/competitor/regulator/internal, `confidence`.

**topic**: `id, title, category` (enum: commercial_terms, payment_terms, delivery_terms, scope, quality_sla, warranty, liability_penalties, ip_rights, confidentiality, compliance, budget_allocation, kpi_targets, resource_allocation, risks, timeline, integration_1c, integration_bitrix, security_specific, other), `status` ∈ open/in_progress/blocked/agreed/formalized/done/cancelled/overdue, `source_quote, source_timestamp, confidence`.

**key_fact**: `fact, quote, timestamp, confidence`.

**excluded_source_range** (или `null`): `from` (timestamp), `to` (необязательно; timestamp | null — null или нет поля: до конца записи; не раньше `from`), `reason`. Цитаты из исключённого фрагмента не используются; та же граница — в `review.yaml → source_scope`: `excluded_from` = `from`, `excluded_until` = `to` (`null` — до конца записи), `exclusion_reason` = `reason`.

**cialdini_tactic / voss_tactic**: `id, tactic_family` (`cialdini` | `voss`), `tactic_type` (Чалдини: reciprocity, commitment_consistency, social_proof, authority, liking, scarcity, unity; Восс: mirroring, labeling, tactical_empathy, accusation_audit, calibrated_question, thats_right, late_night_dj, no_oriented_question, anchoring_extreme, loss_aversion, other), `used_by` (person_id), `target` (person_id | null), `source_quote, source_timestamp, effect` ∈ worked/partial/failed/neutral/unclear, `counter_move` (null если не было), `confidence, explanation`.

**manipulation_item**: `id, type` (false_urgency, false_scarcity, anchoring_deceptive, false_dilemma, salami_slicing, gaslighting, false_consensus, bait_and_switch, moving_goalposts, foot_in_the_door, door_in_the_face, guilt_tripping, fait_accompli, reference_pressure, other), `used_by, target` (person_id | null), `source_quote, source_timestamp, how_recognized, response_by_us` (null если не было), `effect` ∈ worked/neutralized/failed/unclear, `confidence, severity` ∈ low/medium/high. Порог включения в отчёт: confidence ≥ probable и severity ≥ medium; остальное — в очередь проверки.

**argument_item**: `id, speaker` (person_id), `topic, claim, evidence` (текст: на что опирается утверждение — не объект цитаты), `warrant, backing` (null если нет), `counter_argument` (null если нет), `strength` (целое 1–5), `source_quotes[]` (`{quote, timestamp}`, минимум одна; это слова `speaker`), `confidence`.

**power**: `talk_time_share[{person_id, share_pct}]` (0–100), `question_marks_in_transcript[{person_id, count}]` (count ≥ 0), `interruptions[{who, whom, count}]` (count ≥ 1), `dominant_speaker` (person_id | null), `framing_control` (person_id | null), `framing_moments[{speaker, quote, timestamp, description}]`, `concessions[{speaker, quote, timestamp, prior_position_quote, prior_position_timestamp, prior_position_speaker}]` (`prior_position_speaker` необязательно). `evidence_note` — как считались доли (по словам / по репликам) и ограничения.
- `quote` уступки — слова `speaker`. Прежняя позиция — по умолчанию тоже его слова. Если её
  назвал коллега или другая сторона («вместо тридцати» сказала одна, на 45 дней согласился
  другой), его person_id — в `prior_position_speaker`: атрибуция `prior_position_quote`
  проверяется по нему; `null` или нет поля — по `speaker`.

**sentiment_point**: `timestamp_start, timestamp_end, speaker, valence` (−1…1), `arousal` (0…1), `dominant_emotion` ∈ neutral/satisfaction/interest/tension/stress/frustration/anger/amusement/other, `stress_markers[], quote`. `overall_summary.overall` ∈ collaborative/neutral/tense/hostile.

**commitment_item**: `id, meeting_id, type` (deliver_document, make_payment, approve_decision, conduct_action, provide_information, sign_contract, deliver_goods_services, schedule_meeting, other), `who` (person_id), `to_whom` (person_id | null), `what, deadline` (ISO-дата | null — вычислять только из явно названной даты; «к пятнице» → null + `deadline_verbatim`), `deadline_verbatim, status` ∈ open/in_progress/blocked/agreed/formalized/done/cancelled/overdue (на момент встречи обычно `open` или `agreed`), `value_rub` (число | null), `source_quote, source_timestamp, confidence, linked_topic` (topic_id), `follow_up{reminder_date, reminder_sent: false, actual_completion_date: null, completion_notes: null}`; необязательно `related_mentions[{quote, timestamp, note}]` (`note` необязательно).
- `what` — только актуальная формулировка обязательства: без истории, прежних сроков и объёмов.
  Прежние версии, предложение, принятие или подтверждение другой стороной, повтор в итогах —
  в `related_mentions`, по одной реплике на элемент; в `note` — чем она связана: «предложено»,
  «принято», «подтверждено в итогах», «изменено: прежняя версия».
- Цитаты `related_mentions` проверяются по транскрипту и таймкоду, как остальные, но без
  атрибуции к `who`: это обычно реплики других людей.

**open_question**: `topic, question, quote, timestamp`.

**risk_item**: `id, type` (financial, contractual, operational, reputational, compliance_legal, counterparty, technology, security_physical, security_information, human_capital, other), `description, first_seen_meeting_id, last_seen_meeting_id, involved_companies[], involved_people[], linked_topic, likelihood` ∈ low/medium/high, `impact_rub` (число | null), `impact_qualitative` ∈ low/medium/high, `severity` ∈ low/medium/high, `mitigation{planned_actions, owner, deadline}` (`planned_actions` — непустая строка; `owner` и `deadline` могут быть null), `status` ∈ open/mitigating/closed/realized, `evidence[{quote, meeting_id, timestamp}]` (минимум одна), `confidence`. Если риск уже есть в `knowledge/risks/active.jsonl` — использовать его `id` и обновить `last_seen_meeting_id`.

## Три производных представления (из 01–08, без новых цитат «по смыслу»)

| Файл | `view_type` | Обязательные поля |
|---|---|---|
| `09_decision_authority.json` | `decision_authority_map` | `status` ∈ fully_defined/partially_defined/undefined, `decision_areas[]`, `authority_observations[]`, `unresolved[]` |
| `10_argument_map.json` | `argument_map` | `argument_clusters[]` |
| `11_meeting_effectiveness.json` | `meeting_effectiveness` | `method, dimensions[], overall_score_0_5, calculation, classification` ∈ effective/partial_effectiveness/low_effectiveness, `strengths[], losses[], next_meeting_protocol[], confidence` |

**decision_area**: `topic, decision, options[], current_state` (начинается со статусного префикса: «Принято: …», «Не решено: …», «Отложено: …», «Отменено: …»; принятым отчёт и follow-up считают решение только по префиксу «Принято», «Решено», «Согласовано» или «Утверждено» и «:», «.» или «—» после него; без префикса — открытое), `final_decision_maker` (person_id | null), `advisory_participants[], blocking_rights` (строка | null — у кого право блокировать и на чём основано), `proposal_owner` (person_id | null), `proposal_owner_source` ∈ transcript/user_confirmation/null, `deadline` (ISO-дата | null), `deadline_source` ∈ transcript/user_confirmation/null, `evidence[]` (`{quote, timestamp}`), `confidence`. Не назначать ЛПР по должности — только по сказанному; иначе `null` + пункт в `unresolved`.

**authority_observation**: `person_id, observed_role, formal_authority_in_decision` (строка | null), `evidence[]` (`{quote, timestamp}`), `confidence`.

**argument_cluster**: `topic, question, positions[{argument_id, speaker, position, supports[], opposes[], strength (1–5), missing_evidence, evidence[], confidence}], resolution` ∈ resolved/partially_resolved/not_resolved, `resolution_note`. Цитаты `evidence` позиции — слова её `speaker`.

**meeting_effectiveness**: `method` = «Рабочая диагностическая шкала 0–5; не является стандартизированным психологическим или организационным тестом»; `dimensions` — ровно шесть измерений: `agenda_coverage, decision_closure, actionability, role_clarity, evidence_quality, participation_balance`, каждое `{dimension, score_0_5, basis, evidence_refs[]}` (`evidence_refs` — минимум одна ссылка); `overall_score_0_5` — среднее арифметическое, `calculation` — формула с числами; классификация: ≥ 3.5 effective, 2.0–3.5 partial_effectiveness, < 2.0 low_effectiveness.

## Сопутствующие файлы встречи

- `meta.yaml` — id, дата, тип, участники, цель, чувствительность (скелет создаёт `na.py source create`).
- `review.yaml` — карта спикеров, шлюз согласия, границы фрагмента, последующие подтверждения пользователя (`source: user_confirmation`), открытые вопросы к пользователю.
- `analysis/validation_report.json` — результат `na.py validate`.
- `report_auto.md` (скрипт) → `report.md` (финальная редакция с разделением фактов, интерпретаций и подтверждений).
- `follow_up.json`, `follow_up_protocol.md`, `follow_up_message.md` — всегда `draft_not_sent`.
- `knowledge_proposal.json` → `knowledge_diff.md` — проект изменений базы (навык `knowledge-update`).
- `run_manifest.json` — версии контрактов, хеши входов, этапы, `resume_from`.
