| Параметр | Значение |
|----------|----------|
| Версия | 0.1.0 |
| Статус | active |
| Owner | Дмитрий Остапенко |
| Создан | 2026-10-18 |
| Обновлён | 2026-10-18 |
| Назначение | Краткая карта 11 контрактных артефактов анализа встречи: какой файл, какие обязательные поля и enum, откуда берётся содержание. Точный источник истины — `core/contracts/contracts.schema.json`; при расхождении применять схему. |

# Карта контрактов анализа (contract version 1.0.0)

Общие типы: `confidence` ∈ `certain | probable | possible | speculative`;
`evidence` = `{quote, timestamp}`; `timestamp` — `HH:MM:SS` (или `MM:SS` как в транскрипте);
`nullable_*` — значение или `null`. Во всех файлах `meeting_id` = каталог встречи.
Идентификаторы: `person_id` — slug латиницей (`ivan-petrov-primer-holding`), `company_id` —
slug организации, `topic_id` — slug темы; те же id используются в `knowledge/`.

## Восемь основных артефактов (из транскрипта)

| Файл | Метод (промпт) | Обязательные поля верхнего уровня |
|---|---|---|
| `01_extraction.json` | B.1 + общая экстракция | `participants[]`, `companies[]`, `topics[]`, `key_facts[]`, `outcome` ∈ agreed/partial/deferred/failed/no_decision, `outcome_summary`, `excluded_source_range` (объект `{from, to, reason}` или null) |
| `02_tactics.json` | B.3, B.4 | `cialdini[]`, `voss[]`, `debug_possible[]` (слабые сигналы confidence ≤ possible) |
| `03_manipulations.json` | B.5 | `manipulations[]`, `conclusion` |
| `04_arguments.json` | B.6 | `arguments[]` |
| `05_power.json` | B.7 | `talk_time_share[]`, `question_marks_in_transcript[]`, `interruptions[]`, `dominant_speaker`, `framing_control`, `framing_moments[]`, `concessions[]`, `evidence_note` |
| `06_emotions.json` | B.8 | `source` ∈ text_only/audio/both, `sentiment_points[]`, `overall_summary{overall, most_negative_timestamp, most_tense_timestamp, general_trend}`, `limitations` |
| `07_commitments.json` | B.2 | `commitments[]`, `open_questions[]` |
| `08_risks.json` | из 01–07 | `risks[]` |

### Элементы

**participant**: `person_id, full_name, role` (enum: owner, founder, owner_founder, CEO, board_chair, board_member, CFO, COO, CTO_CIO, CSO, business_unit_head, functional_head, treasury_head, budgeting_head, accounting_head, procurement_head, legal_head, sales_director, key_account_manager, project_manager, government_customer_representative, government_official, regulator_representative, lawyer, engineer, external_advisor, other), `role_freeform, company_id, is_our_side, identity_source` (как установлена личность: self_introduction / addressed_by_name / user_confirmation / inferred), `confidence`.

**company**: `id, legal_name, short_name, inn` (null если не назван), `is_our_side, relation_type, confidence`.

**topic**: `id, title, category, status, source_quote, source_timestamp, confidence`.

**key_fact**: `fact, quote, timestamp, confidence`.

**cialdini_tactic / voss_tactic**: `id, tactic_family` (`cialdini` | `voss`), `tactic_type` (Чалдини: reciprocity, commitment_consistency, social_proof, authority, liking, scarcity, unity; Восс: mirroring, labeling, tactical_empathy, accusation_audit, calibrated_question, thats_right, late_night_dj, no_oriented_question, anchoring_extreme, loss_aversion, other), `used_by` (person_id), `target` (person_id | null), `source_quote, source_timestamp, effect` ∈ worked/partial/failed/neutral/unclear, `counter_move` (null если не было), `confidence, explanation`.

**manipulation_item**: `id, type` (false_urgency, false_scarcity, anchoring_deceptive, false_dilemma, salami_slicing, gaslighting, false_consensus, bait_and_switch, moving_goalposts, foot_in_the_door, door_in_the_face, guilt_tripping, fait_accompli, reference_pressure, other), `used_by, target, source_quote, source_timestamp, how_recognized, response_by_us, effect` ∈ worked/neutralized/failed/unclear, `confidence, severity` ∈ low/medium/high. Порог включения в отчёт: confidence ≥ probable и severity ≥ medium; остальное — в очередь проверки.

**argument_item**: `id, speaker, topic, claim, evidence, warrant, backing, counter_argument, strength` (1–5), `source_quotes[]` (evidence), `confidence`.

**power**: `talk_time_share[{person_id, share_pct}]`, `question_marks_in_transcript[{person_id, count}]`, `interruptions[{who, whom, count}]`, `framing_moments[{speaker, quote, timestamp, description}]`, `concessions[{speaker, quote, timestamp, prior_position_quote, prior_position_timestamp}]`. `evidence_note` — как считались доли (по словам / по репликам) и ограничения.

**sentiment_point**: `timestamp_start, timestamp_end, speaker, valence` (−1…1), `arousal` (0…1), `dominant_emotion, stress_markers[], quote`.

**commitment_item**: `id, meeting_id, type` (deliver_document, make_payment, approve_decision, conduct_action, provide_information, sign_contract, deliver_goods_services, schedule_meeting, other), `who` (person_id), `to_whom` (person_id | null), `what, deadline` (ISO-дата | null — вычислять только из явно названной даты; «к пятнице» → null + `deadline_verbatim`), `deadline_verbatim, status` ∈ open/in_progress/blocked/agreed/formalized/done/cancelled/overdue (на момент встречи обычно `open` или `agreed`), `value_rub` (число | null), `source_quote, source_timestamp, confidence, linked_topic` (topic_id), `follow_up{reminder_date, reminder_sent: false, actual_completion_date: null, completion_notes: null}`.

**open_question**: `topic, question, quote, timestamp`.

**risk_item**: `id, type` (financial, contractual, operational, reputational, compliance_legal, counterparty, technology, security_physical, security_information, human_capital, other), `description, first_seen_meeting_id, last_seen_meeting_id, involved_companies[], involved_people[], linked_topic, likelihood` ∈ low/medium/high, `impact_rub` (число | null), `impact_qualitative, severity` ∈ low/medium/high, `mitigation{planned_actions, owner, deadline}` (null-ы допустимы внутри), `status` ∈ open/mitigating/closed/realized, `evidence[{quote, meeting_id, timestamp}]`, `confidence`. Если риск уже есть в `knowledge/risks/active.jsonl` — использовать его `id` и обновить `last_seen_meeting_id`.

## Три производных представления (из 01–08, без новых цитат «по смыслу»)

| Файл | `view_type` | Обязательные поля |
|---|---|---|
| `09_decision_authority.json` | `decision_authority_map` | `status` ∈ fully_defined/partially_defined/undefined, `decision_areas[]`, `authority_observations[]`, `unresolved[]` |
| `10_argument_map.json` | `argument_map` | `argument_clusters[]` |
| `11_meeting_effectiveness.json` | `meeting_effectiveness` | `method, dimensions[], overall_score_0_5, calculation, classification` ∈ effective/partial_effectiveness/low_effectiveness, `strengths[], losses[], next_meeting_protocol[], confidence` |

**decision_area**: `topic, decision, options[], current_state, final_decision_maker, advisory_participants[], blocking_rights[], proposal_owner, proposal_owner_source, deadline, deadline_source, evidence[], confidence`. Не назначать ЛПР по должности — только по сказанному; иначе `null` + пункт в `unresolved`.

**authority_observation**: `person_id, observed_role, formal_authority_in_decision, evidence[], confidence`.

**argument_cluster**: `topic, question, positions[{argument_id, speaker, position, supports[], opposes[], strength, missing_evidence, evidence[], confidence}], resolution, resolution_note`.

**meeting_effectiveness**: `method` = «Рабочая диагностическая шкала 0–5; не является стандартизированным психологическим или организационным тестом»; `dimensions` — шесть измерений: `agenda_coverage, decision_closure, actionability, role_clarity, evidence_quality, participation_balance`, каждое `{dimension, score_0_5, basis, evidence_refs[]}`; `overall_score_0_5` — среднее арифметическое, `calculation` — формула с числами; классификация: ≥ 3.5 effective, 2.0–3.5 partial_effectiveness, < 2.0 low_effectiveness.

## Сопутствующие файлы встречи

- `meta.yaml` — id, дата, тип, участники, цель, чувствительность (скелет создаёт `na.py source create`).
- `review.yaml` — карта спикеров, шлюз согласия, границы фрагмента, последующие подтверждения пользователя (`source: user_confirmation`), открытые вопросы к пользователю.
- `analysis/validation_report.json` — результат `na.py validate`.
- `report_auto.md` (скрипт) → `report.md` (финальная редакция с разделением фактов, интерпретаций и подтверждений).
- `follow_up.json`, `follow_up_protocol.md`, `follow_up_message.md` — всегда `draft_not_sent`.
- `knowledge_proposal.json` → `knowledge_diff.md` — проект изменений базы (навык `knowledge-update`).
- `run_manifest.json` — версии контрактов, хеши входов, этапы, `resume_from`.
