# Миграция онтологий в универсальное ядро

Этап 2 «Универсальная модель». Две закрытые референсные онтологии сведены в
одну core-онтологию и подключаемые профили:

| Источник | Версия | Характер | Куда ушло в целом |
|---|---|---|---|
| CFO-онтология (source-core) | 0.4, 2026-08-09 | детальная: enum-словари, сущности анализа встречи, правила извлечения, пороги тактик и манипуляций, risk matrix, производные представления, локальные пути | `core/ontology/core.ontology.yaml` (сущности, enum, правила, пороги, views); CFO-специфика → `profiles/examples/cfo-example/`; пути → отброшены |
| Онтология владельца/CEO (reference-profiles) | 1.1.0, 2026-08-12 | evidence-first: классы утверждений, SourceArtifact, EvidenceRef, управленческие объекты, психологические гипотезы, стратегия и её результат; плюс 9 файлов политик профиля | `core/ontology/core.ontology.yaml` (evidence-ядро, управленческие сущности); политики → `core/ontology/profile.schema.json` и `profiles/base/*.yaml`; owner/CEO-специфика → `profiles/examples/owner-ceo-example/` |

Результат:

- `core/ontology/core.ontology.yaml` — 32 сущности, 79 закрытых словарей, 19 представлений, 10 инвариантов, правила связей, извлечения, жизненного цикла, confidence/null, маскирования и психологического слоя.
- `core/ontology/profile.schema.json` — JSON Schema (draft 2020-12) трёх частей профиля: `role`, `organization`, `policy`; правило приоритетов — `x-priority-order`; неослабляемые поля — `x-non-overridable` (в схеме зафиксированы как `const`).
- `profiles/base/{role,organization,policy}.yaml` — универсальные значения по умолчанию.
- `profiles/examples/cfo-example/`, `profiles/examples/owner-ceo-example/` — синтетические профили (вымышленные организации «Пример-Холдинг» и «Демо-Групп», вымышленные лица).
- `docs/ontology_migration.json` — машинно-читаемая таблица (58 правил сопоставления + снимок 1431 идентификатора исходников).
- `scripts/check_ontology_migration.py` — тест отсутствия потерь.

Контракты `core/contracts/*.json` не менялись; все enum ядра сверены с ними поэлементно (0 расхождений).

## Принципы сведения

1. **Evidence-first как база.** Из онтологии владельца взяты `evidence_classes`, `SourceArtifact`, `EvidenceRef`, `unknown_values` и правило «каждый извлечённый объект связан с EvidenceRef». CFO-сущности получили `layers.meeting_analysis` с привязкой к `contracts.schema.json`.
2. **Два контрактных слоя одного объекта.** Там, где два контракта фиксируют разные словари для одного понятия (Commitment, Risk, confidence), ядро хранит оба словаря как слои `meeting_analysis` и `register` с явной таблицей проекции; «родное» значение слоя не перезаписывается.
3. **Имена сущностей — из TARGET_ARCHITECTURE §2.** Прежние имена сохранены в `legacy_aliases` каждой сущности.
4. **Всё локальное — в профили или прочь.** Названия организации, должности, перечни лиц, отраслевые словари и пути каталогов не входят в ядро.
5. **Профиль не ослабляет защиту.** Согласие, маршруты, маскирование, удаление, запреты психологического слоя, пороги доказательности и лицензии — `x-non-overridable`.

## Правило приоритетов (зафиксировано в profile.schema.json)

1. Непересматриваемые ограничения безопасности и лицензии.
2. Явное решение пользователя для конкретного источника или действия.
3. Приватная политика организации.
4. Ролевой профиль.
5. Универсальные значения по умолчанию.

## Таблица переноса: CFO-онтология v0.4

Обозначения целей: **core** — `core.ontology.yaml`; **role** / **org** / **policy** — соответствующая часть `profile.schema.json` и профили; **пример** — только в `profiles/examples/`; **переименовано** — сохранено под новым именем; **заменено** — понятие сохранено другим полем; **отброшено** — см. раздел «Что потеряно».

### meta

| Элемент | Цель | Куда / как |
|---|---|---|
| `meta.version`, `meta.language`, `meta.last_updated` | core | `meta` (версия 1.0.0) |
| `meta.domain` («финансовые и подрядные переговоры …») | org | `organization.group.domain`; пример — `cfo-example/organization.yaml` |
| `meta.owner` («CFO <организация>») | заменено | `role.user_role` (CFO) + `organization.group` |

### enums

| Enum | Цель | Примечание |
|---|---|---|
| `role` (19 значений) | core `enums.role` | расширен до объединённого списка из `contracts.schema.json` (27 значений): добавлены owner, founder, board_chair, business_unit_head, functional_head, government_customer_representative, government_official, regulator_representative |
| `meeting_type` (14) | core | без изменений |
| `topic_category` (19) | core | без изменений; `integration_1c`, `integration_bitrix`, `security_specific` сохранены как контрактные значения с пометкой «пример отраслевого расширения»; отраслевые подкатегории — `organization.topic_subcategories` |
| `status` (8) | core | статус Topic и Commitment в слое анализа встречи |
| `confidence_level` (4) | core `confidence_scales.confidence_level` | вторая шкала `evidence_confidence` — из онтологии владельца; проекция задана |
| `sensitivity` (5) | core | сохранена для совместимости; рекомендовано раздельно `business_confidentiality` + `personal_data_class`; `regulated` ≡ special_category/biometric_or_voice_data |
| `voss_tactic` (11), `cialdini_principle` (7), `manipulation_type` (15) | core | без изменений |
| `risk_type` (11), `commitment_type` (9) | core | без изменений |
| `review_item_status` (4), `review_priority` (4) | core | статусы владельца answered/no_longer_relevant сведены к resolved/dismissed |
| `pipeline_stage_status`, `knowledge_event_entity_type`, `knowledge_target_view` | core | совпадают с `knowledge_event.schema.json` |

### Сущности

| Сущность v0.4 | Цель | Поля и примечания |
|---|---|---|
| `Person` | core `Person` | все поля сохранены: id, full_name, short_name, role, role_freeform, company_id, is_our_side, contacts{email, phone, telegram}, style_markers{speaking_pace, formality, directness, preferred_lang, notes}, negotiation_patterns[], decision_power, known_since, last_met, meetings_count, flags. Добавлены из онтологии владельца: display_name, roles, organizations, identity_confidence, status, evidence, manual_notes. `flags` — открытый словарь, примеры (`flag_examples`) сохранены. `is_our_side` описано как «своя сторона (определяется профилем организации: our_side_rules)» |
| `Company` | переименовано → core `Organization` | все поля сохранены: legal_name, short_name, inn, kpp, industry, is_our_side, relation_type, status, dd_status, dd_findings, key_people, deals_history[deal_id, topic, value_rub, outcome, duration_days], red_flags[description, discovered_meeting_id, severity], notes, first_contact, last_contact. `kpp` помечено как локальный реквизит |
| `Meeting` | core `Meeting` | сохранены: id, title, date, duration_min, meeting_type, channel, location, participants, our_side, counterparty_side, topics, purpose, outcome, outcome_summary, artifacts{transcript, audio, report, analysis}, sensitivity, commitments, tactics_observed, manipulations_observed, risks_identified, power_dynamics_snapshot, sentiment_summary{overall, peaks}, next_meeting_planned, follow_up_needed. `artifacts.note_plaud` → **переименовано** `provider_note`. `plaud_source_id` → **заменено** `SourceArtifact.external_id_or_local_path` (provider=plaud). Добавлены: situation_type, source_ids, masking_mode, transcript_status, scope_start/scope_end, excluded_source_range, review_status, status, decisions, assignments |
| `Topic` | core `Topic` | все поля сохранены. `open_questions[{question, who_owns, deadline}]` → `list(ref(OpenItem))`; поля **переименованы**: question→statement, who_owns→owner, deadline→due_date |
| `Commitment` | core `Commitment` (слой meeting_analysis) | все поля сохранены, включая deadline_verbatim, value_rub, follow_up{reminder_date, reminder_sent, actual_completion_date, completion_notes}. Слой register (из ExternalCommitment): statement, from_party, to_party, value, fulfillment_evidence, register_status; таблица `status_mapping_analysis_to_register` |
| `TacticObservation` | core | все поля; добавлено контрактное `explanation` |
| `ManipulationObservation` | core | все поля (how_recognized, response_by_us, severity) |
| `Argument` | core | все поля; добавлены контрактные `backing`, `confidence` |
| `PowerDynamics` | core | все поля; контрактные имена `concessions` (= moments_of_yield) и `framing_moments` (= shift_moments) добавлены, `question_asker` — производное от question_marks_in_transcript |
| `Risk` | core `Risk` (слой meeting_analysis) | все поля: type, description, first/last_seen_meeting_id, involved_companies, involved_people, linked_topic, likelihood, impact_rub, impact_qualitative, severity, mitigation{planned_actions, owner, deadline}, status(open/mitigating/closed/realized), evidence[quote, meeting_id, timestamp]. Слой register из онтологии владельца: statement, owner, probability, impact, horizon, triggers, mitigations, linked_entities, register_status; таблица проекции статусов |
| `SentimentPoint` | core | все поля; stress_markers{speaking_rate_z, pitch_variance_z, pause_ratio} (в контракте — список строк) |
| `DecisionAuthorityMap` | core | все поля; добавлено `view_type`; из decision_areas создаются кандидаты `Decision(status=proposed)` после подтверждения |
| `ArgumentMap`, `MeetingEffectiveness` | core | все поля; добавлено `view_type` |
| `RunManifest` | core | все поля |
| `ReviewItem` | core `ReviewItem` | `item_id` → **переименовано** `id`; остальные поля сохранены (meeting_id, category, priority, status, question, source, source_path, source_ref, evidence, answer, resolved_at); добавлены created_at, related_entities, available_options |
| `KnowledgeEvent` | core | 1:1 с `knowledge_event.schema.json` |
| `FollowUpPackage` | core | все поля; добавлены decisions, assignments; `delivery_status` только draft_not_sent |

### relations

Все 24 связи сохранены (предикаты belongs_to, has_participants, discusses, produces, exhibits, identifies, has, made_by, relates_to, used_by, evolves_across, first_identified_in, exhibits_repeatedly, employs, snapshots, resolved_by); `Company employs` → `Organization employs`. Добавлены связи для Decision, Assignment, Initiative, OpenItem, Observation, Hypothesis, StakeholderPosition, Strategy, StrategyOutcome, EvidenceRef.

### extraction_rules

| Группа | Цель | Изменение формулировки |
|---|---|---|
| general (4) | core | без содержательных изменений |
| person (3) | core | добавлено правило: `is_our_side` — по our_side_rules профиля, не по догадке |
| commitment (3) | core | «open_question для Topic» → `OpenItem(item_type=question)` |
| tactic (3) | core | без изменений; порог `confidence ≥ probable` — `thresholds.tactic_min_confidence` (raise_only) |
| manipulation (3) | core | «не показываем в отчёте для CFO» → «в основном отчёте»; пороги — `thresholds.manipulation_min_confidence/min_severity` (raise_only) |
| risk (3) | core | без изменений |
| privacy (2) | core + policy | порог «>5 млн руб» → `policy.privacy.defaults.strict_confidential_amount_threshold` (в ядре `null`, в cfo-example — 5 000 000 RUB); «не сотрудник <организация>» → `is_our_side=false` по профилю организации; маскирование «первая буква фамилии» → `masking_rules.modes.names_only` и `policy.masking.names_only_format` |

### risk_matrix

Все 9 ячеек → core `risk_matrix` без изменений.

### knowledge_paths (21)

Отброшено — см. «Что потеряно». `analysis_contracts` заменено ссылкой `meta.contracts.meeting_analysis` на `core/contracts/contracts.schema.json`.

### derived_views → core `views`

| Представление v0.4 | Цель | Примечание |
|---|---|---|
| `person_dossier` | core `views.person_dossier` | inputs/outputs сохранены; `query` отброшен |
| `company_dossier` | переименовано `organization_dossier` | `query` отброшен |
| `meeting_prep_brief` | переименовано `pre_meeting_brief` | объединено с `pre_meeting_one_page_brief` владельца; `output` → `outputs` |
| `monthly_patterns` | переименовано `periodic_review` | `period` → `periods: [weekly, monthly]`; `top_5_manipulators` → `top_5_by_manipulation_observations` (человек не называется манипулятором); остальные outputs сохранены |
| `cross_meeting_comparison` | core | полностью, включая `minimum_meetings: 2` и правило baseline |
| `project_control_center` | переименовано `control_center` | inputs `negotiations` → `meetings`; outputs сохранены; `storage.*` отброшено |
| `review_queue` | core | inputs/outputs/rule сохранены; `storage` отброшено |
| `full_text_search` | переименовано `evidence_search` | filters сохранены; `storage` отброшено |
| `event_projection`, `pipeline_run_status`, `follow_up_package` | core | полностью; `storage` отброшено |

### changelog

Структура записи (version, date, author, changes, triggers) сохранена; история версий 0.1–0.4 закрытого проекта в ядро не переносится (происхождение зафиксировано здесь и в `meta`/`changelog` ядра).

## Таблица переноса: онтология владельца/CEO v1.1.0

### Верхний уровень

| Элемент | Цель | Примечание |
|---|---|---|
| `meta.name` | core | заменено на универсальное имя без названия организации |
| `meta.evidence_first`, `meta.unknown_values` (unknown, not_stated, requires_confirmation) | core | без изменений |
| `evidence_classes` (6) | core | дословно |
| `confidence_levels` (high, medium, low, not_assessed) | core `confidence_scales.evidence_confidence` | плюс проекция из CFO-шкалы |
| `source_providers` (9) | core | без изменений |
| `management_situation_types` (13) | переименовано `enums.management_situation_type` | значения сохранены; политика закрытого словаря — `organization.situation_type_extensions` только с `confirmation_source: user_confirmation` |
| `relationship_rules` (10) | core `relationship_rules` | NegotiationStrategy → Strategy в формулировках; добавлены 3 правила из системного объёма профиля |
| `strategy_memory` | переименовано `lifecycle_rules.strategy_reuse` | `future_read_rule`, `save_gate` сохранены; `archive_path`, `reusable_index`, `outcome_path` отброшены (пути) |
| `change_log` | переименовано `changelog` | `change` → `changes` |

### Сущности

| Сущность v1.1.0 | Цель | Поля и примечания |
|---|---|---|
| `SourceArtifact` | core | required и fields дословно (external_id_or_local_path, content_sha256, canonical_source_id, duplicate_of, masking_mode, consent_reference); добавлены из политик: business_confidentiality, personal_data_class, privacy_gate_decision, retention_decision, deletion_record |
| `EvidenceRef` | core | дословно; добавлен опциональный `meeting_id` для сокращённой формы контракта анализа |
| `Person` | core `Person` | display_name, roles, organizations, identity_confidence, evidence, manual_notes — объединено с Person CFO |
| `Organization` | core `Organization` | legal_name, organization_type, parent_id, evidence — объединено с Company CFO |
| `GovernanceBody` | core `GovernanceBody` | дословно; экземпляры органов — `organization.governance_bodies` |
| `Meeting` | core `Meeting` | `datetime` → **переименовано** `date`; situation_type, participants, transcript_status, scope_start/end, masking_mode, review_status, status, source_ids сохранены |
| `DecisionOption` | core `DecisionOption` | дословно; `status` — свободная строка (словарь не задан в источнике) |
| `ExecutiveDecision` | переименовано `Decision` | все поля и 6 статусов; добавлены options, meeting_id, decision_type (словарь — `organization.decision_types`) |
| `Assignment` | core | дословно, включая `done_rule` и 9 статусов |
| `StrategicInitiative` | переименовано `Initiative` | все поля и 7 статусов |
| `ExternalCommitment` | переименовано `Commitment` (слой register) | statement, from_party, to_party, deadline, value, fulfillment_evidence, 7 статусов |
| `Risk` | core `Risk` (слой register) | statement, owner, probability, impact, horizon, triggers, mitigations, linked_entities, 7 статусов |
| `Opportunity` | core | дословно |
| `StakeholderPosition` | core | дословно |
| `OpenItem` | core | дословно; поглощает `Topic.open_questions` CFO |
| `BehaviorObservation` | переименовано `Observation` | observation, context, pattern_status; добавлены meeting_id, dimension |
| `PsychologicalHypothesis` | переименовано `Hypothesis` | все required (evidence_for, evidence_against, alternatives, practical_use, confidence), status (5), `clinical_claim: forbidden_unless_verified_document`; добавлены dimension, last_reviewed, review_date |
| `NegotiationStrategy` | переименовано `Strategy` | все поля; `canon_basis` → **переименовано** `method_basis` (+ `module: enum(advisory_module)`), `canon_files` → `method_files`; добавлено required `advisory_module` (dialogue_contour / negociar_ru / negociar_upstream) |
| `StrategyOutcome` | core | дословно; описание обобщено (без имени пользователя) |
| `ReviewItem` | core `ReviewItem` | id, question, status, created_at, related_entities, available_options, evidence; статусы `answered` → resolved, `no_longer_relevant` → dismissed (legacy_aliases) |

### Политики профиля владельца (profile/*.yaml) → profile.schema.json

Эти файлы не входят в loss-тест (он покрывает только два ontology.yaml), но полностью разложены по схеме профиля:

| Файл политики | Цель | Примечание |
|---|---|---|
| `owner_profile.yaml` | role (`user_role`, `additional_roles`, `confirmed_responsibility_areas`, `scope`, `not_confirmed`) | реальное лицо и организация не переносятся; пример — `owner-ceo-example/role.yaml` |
| `system_scope.yaml` | role (`enabled_modules`, `tracked_object_types`, `tracked_behavior_dimensions`) + org (`hierarchy`, `scope`, `pending_scope_decisions`, `situation_type_extensions`) + core (`management_situation_type`, relationship_rules) | `negociar_strategic_advisory` → `strategic_advisory` (модуль выбирается в `policy.advisory`) |
| `interface_policy.yaml` | core `views` (13 представлений, `owner_` и `negociar_` префиксы сняты) + policy.interface (response_format, detail_disclosure, disclosure_rules, view_consistency_rules) + role.priority_views | |
| `privacy_policy.yaml` | policy.privacy + core enums (business_confidentiality, personal_data_class, masking_mode, processing_service, processing_route) + `SourceArtifact.privacy_gate_decision` | `chatgpt` сохранён, добавлен `claude_code` (целевая среда по DECISIONS.md) |
| `source_policy.yaml` | policy.sources + core `source_type` + `SourceArtifact` | аккаунт-специфичные факты (план, статус подключения, base_url, auth_header, документация) не переносятся: адаптеры — этап 9 |
| `retention_policy.yaml` | policy.retention + core enums (retention_decision, deletion_location) + `SourceArtifact.retention_decision/deletion_record` | «owner» → «user» |
| `psychological_analysis_policy.yaml` | core `psychological_layer` (уровни, запреты, разделение) + policy.psychological_analysis (depth, allowed_dimensions, verified_clinical_fact_storage) | шкала confidence гипотез унифицирована с `evidence_confidence` |
| `advisory_policy.yaml` | policy.self_critique (бывш. owner_critique, без субъекта) + policy.decision_authority | |
| `negociar_policy.yaml` | policy.advisory (modules_allowed, trigger_scope, evidence_boundary, response_contract, exact_message_gate, storage, save_and_reuse_gate, outcome_learning, safety, license_terms) | формулировки обобщены под любой методический модуль; лицензионные условия CC BY-NC-SA 4.0 — const |

## Спорные решения при сведении

1. **Две шкалы confidence.** Оба контракта зафиксированы: `certain/probable/possible/speculative` (анализ встречи) и `high/medium/low/not_assessed` (EvidenceRef, реестр). Ядро хранит обе и задаёт однонаправленную проекцию (possible и speculative → low). Гипотезы (`Hypothesis`) используют шкалу реестра, хотя политика психологического анализа владельца перечисляла CFO-шкалу — внутри самого референса было противоречие с его же ontology.yaml.
2. **Commitment и Risk с двумя словарями статусов.** Вместо выбора одного словаря ядро описывает оба слоя и таблицу проекции при первичном внесении в реестр (`blocked` → `accepted` + OpenItem(blocker); `mitigating` → `monitoring`). Исходный статус анализа не перезаписывается.
3. **`GovernanceBody` оставлен в ядре** как тип (орган управления есть у любой организации), а перечень реальных органов — в профиле организации.
4. **`ReviewItem.status`:** словарь CFO (`open/resolved/dismissed/deferred`) выбран каноническим; `answered` и `no_longer_relevant` владельца — алиасы.
5. **Отраслевые значения `integration_1c`, `integration_bitrix`, `security_specific`** нельзя убрать из ядра, не меняя зафиксированный контракт; они остаются с пометкой, а отраслевые уточнения живут в `organization.topic_subcategories`.
6. **`sensitivity.regulated`** смешивает деловую конфиденциальность и класс ПДн; оставлено для совместимости, параллельно введены раздельные поля.
7. **`top_5_manipulators`** переименовано: противоречило правилу ядра «никогда не называем человека манипулятором».

## Что потеряно

Ни одна сущность, поле, enum-значение, связь, правило или статус не отброшены без фиксации. Ниже — единственные элементы, сознательно не перенесённые в ядро, каждый с причиной (все они подтверждаются `scripts/check_ontology_migration.py` как задокументированные отбрасывания или замены):

| Элемент | Источник | Причина |
|---|---|---|
| `knowledge_paths.*` (20 путей) | CFO | Локальная раскладка каталогов личного проекта (knowledge/, negotiations/, system/). Runtime state и пути не входят в устанавливаемый продукт (TARGET_ARCHITECTURE); раскладка рабочего каталога определяется на этапе 6. `analysis_contracts` заменён ссылкой на `core/contracts`. |
| `derived_views.*.query` (2) | CFO | SQL-подобные псевдозапросы — деталь реализации read model; семантика сохранена в `views.*.inputs/outputs`. |
| `derived_views.*.storage` и `project_control_center.storage.*` (9) | CFO | Локальные пути хранения представлений; раскладка — этап 6. |
| `strategy_memory.archive_path`, `reusable_index`, `outcome_path` (3) | владелец/CEO | Локальные пути; правила чтения и сохранения стратегии сохранены в `lifecycle_rules.strategy_reuse`. |
| `meta.owner` | CFO | Владелец онтологии — не свойство универсальной модели; заменено `role.user_role` + `organization.group`. |
| `Meeting.plaud_source_id` | CFO | Провайдер-специфичное поле; заменено `SourceArtifact.external_id_or_local_path` при `provider=plaud`. |
| История changelog v0.1–v0.4 и change_log v1.1.0 (содержимое записей) | оба | Приватная история проектов; структура записи сохранена, происхождение зафиксировано в этом документе. |
| Содержание политик, относящееся к аккаунтам и реальным лицам: субъект критики, ФИО владельца, название группы, план провайдера, статус подключения, base_url и ссылки на документацию | владелец/CEO | Персональные и аккаунт-специфичные факты не входят в дистрибутив (DATA_PRIVACY_BOUNDARY); адаптеры провайдеров — этап 9. |

Ничего другого не потеряно.

## Проверка

```text
python3 scripts/check_ontology_migration.py
python3 scripts/check_ontology_migration.py --cfo-source <путь к CFO ontology.yaml> --executive-source <путь к ontology.yaml владельца>
python3 scripts/check_ontology_migration.py --cfo-source ... --executive-source ... --update-snapshot
```

Что делает скрипт:

1. Собирает идентификаторы (сущности, поля, enum-значения, предикаты связей, ключи разделов, текстовые правила) из двух исходных `ontology.yaml` собственным построчным парсером — без PyYAML. Если исходники не переданы (в публичном репозитории их нет), используется снимок 1431 идентификатора из `docs/ontology_migration.json`; при наличии исходников снимок сверяется с ними.
2. Для каждого идентификатора находит самую конкретную запись `entries` таблицы и проверяет, что идентификатор (или его `renamed_to`/`replaced_by`) встречается целым словом в `core.ontology.yaml` (без комментариев), `profile.schema.json`, `profiles/**/*.yaml` или `core/contracts/*.json`. Записи `dropped`/`replaced` обязаны иметь `reason` и выводятся отдельным списком.
3. При наличии PyYAML (опциональная зависимость, только для этого шага) валидирует все профили `profiles/base` и `profiles/examples` по `profile.schema.json` встроенным минимальным валидатором подмножества JSON Schema (type, required, properties, additionalProperties, enum, const, items, minItems, minLength, minimum, anyOf, oneOf, $ref). Без PyYAML шаг пропускается с предупреждением.
4. `--forbidden-pattern` — дополнительная проверка приватности по регулярному выражению (сами запрещённые имена в репозитории не хранятся).

Код выхода 0 — потерь нет. Текущий результат: 1431 идентификатор проверен, 58 записей таблицы, 38 задокументированных отбрасываний/замен, все 9 профилей валидны.
