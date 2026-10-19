| Параметр | Значение |
|----------|----------|
| Версия | 0.1.0 |
| Статус | active |
| Owner | Дмитрий Остапенко |
| Создан | 2026-10-19 |
| Обновлён | 2026-10-19 |
| Назначение | Отчёт выравнивающего прохода по терминам (словарь draft-2): какие варианты терминов были в корпусе, что заменено, какие исключения оставлены и что вынесено на решение Дмитрию. |

# Отчёт выравнивающего прохода по терминам (словарь draft-2)

Проход по `translation-negociar-ru/glossary.md` (раздел «Решения выравнивающего прохода (draft-2)» и корневые
принципы) и `translation-negociar-ru/README.md` («Выравнивающий проход»). Меняются только термины; смысл,
ссылки `[[…]]` и номера занятий не трогались.

## Метод

- Скрипт поиска: `align_terms.py` (scratchpad сессии: `/tmp/claude-0/-home-claude/06a96176-2836-5dc3-8e9f-937257a83430/scratchpad/align_terms.py`), команды
  `scan --out hits.json` и `table --hits hits.json`. Поиск — регулярные выражения по основам слов
  (все падежи), без учёта регистра; совпадения внутри ссылок `[[…]]` и в английской половине
  двуязычных заголовков (`## Русский / English`) не считаются.
- Корпус: `translation-negociar-ru/canon-units/*.ru.json` (`preamble_ru`, `units[].title_ru`, `units[].body_ru`;
  изъятая единица 05 #8 пропущена), `translation-negociar-ru/titles.ru.json`, `translation-negociar-ru/cases/accepted/batch-00{1..6}.ru.json`
  (`title_ru`, `body_ru`; изъятый случай 00438891:400 в принятых пакетах отсутствует),
  `translation-negociar-ru/cases/titles.ru.json`, `skills/negociar-ru/data/canon/{README,SKILL-SPEC,RECONCILIACION,EVALS}.md`,
  `skills/negociar-ru/SKILL.md`.
- Тип варианта: **запрещён** — форма из колонки «Не использовать» словаря (или корневого раздела);
  **проверить** — форма, которая бывает и термином, и обычным словом (например «шеф» — обращение
  или шеф-повар; «менеджер» — gerente или ejecutivo; «власть» в D6 — poder или autoridad); каждое
  такое вхождение сверено с исходником (`*.src.json`) в контексте.

## До прохода

Найдено вхождений: 786 (запрещённых форм — 200, требующих проверки в контексте — 586).
Термины словаря, по которым альтернативных форм не найдено: «образ мира» / «мировоззрение»,
«присоединиться» без вариантов («войти в положение» — 0), «захват ценности», «коммерсант»,
«управленческий бой / менеджерская драка», «прочное и полое», «мягко снаружи, жёстко внутри»,
«сила равнодушия», «путь к жизни», «инверсия ролей», «фрейминг» вне случаев 00438842 и заголовка 07 #1.

| Термин | Вариант | Тип | Файл | Вхождений |
|---|---|---|---|---:|
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `skills/negociar-ru/SKILL.md` | 2 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/canon-units/02-imagen-mundo-picture-world.md.ru.json` | 4 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/canon-units/03-dinamica-roles-role-dynamics.md.ru.json` | 9 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/canon-units/05-ventas-complejas-complex-sales.md.ru.json` | 8 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/canon-units/06-autoridad-distanciamiento-authority-distancing.md.ru.json` | 16 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/canon-units/07-tecnologia-social-social-technology.md.ru.json` | 5 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/canon-units/08-gestion-conflicto-conflict-management.md.ru.json` | 2 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 14 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 2 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 3 |
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | `translation-negociar-ru/titles.ru.json` | 1 |
| jefe → руководитель (повествование, роль, метка) | начальник производства (только jefe de planta) | проверить | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 32 |
| jefe → руководитель (повествование, роль, метка) | начальник производства (только jefe de planta) | проверить | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 11 |
| jefe → руководитель (повествование, роль, метка) | начальник производства (только jefe de planta) | проверить | `translation-negociar-ru/cases/titles.ru.json` | 1 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `skills/negociar-ru/data/canon/EVALS.md` | 2 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/canon-units/01-fundamentos-lucha-fundamentals-management-fight.md.ru.json` | 10 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/canon-units/02-imagen-mundo-picture-world.md.ru.json` | 2 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/canon-units/03-dinamica-roles-role-dynamics.md.ru.json` | 8 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 6 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/canon-units/06-autoridad-distanciamiento-authority-distancing.md.ru.json` | 4 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/canon-units/07-tecnologia-social-social-technology.md.ru.json` | 2 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 25 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 8 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 15 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 6 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | `translation-negociar-ru/cases/titles.ru.json` | 2 |
| jefe (обращение) → шеф | «…, руководитель» в обращении | запрещён | `translation-negociar-ru/cases/accepted/batch-001.ru.json` | 6 |
| jefe (обращение) → шеф | «…, руководитель» в обращении | запрещён | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 1 |
| jefe (обращение) → шеф | «…, руководитель» в обращении | запрещён | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 3 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/canon-units/01-fundamentos-lucha-fundamentals-management-fight.md.ru.json` | 2 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/canon-units/03-dinamica-roles-role-dynamics.md.ru.json` | 6 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 4 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/canon-units/05-ventas-complejas-complex-sales.md.ru.json` | 6 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/canon-units/09-productividad-habitos-productivity-habits.md.ru.json` | 1 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/canon-units/11-capital-social-social-capital.md.ru.json` | 1 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/cases/accepted/batch-001.ru.json` | 23 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 72 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 62 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 13 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/cases/accepted/batch-005.ru.json` | 2 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 1 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | `translation-negociar-ru/cases/titles.ru.json` | 18 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `skills/negociar-ru/SKILL.md` | 1 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `translation-negociar-ru/canon-units/01-fundamentos-lucha-fundamentals-management-fight.md.ru.json` | 1 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 1 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 12 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 26 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `translation-negociar-ru/cases/accepted/batch-005.ru.json` | 4 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 7 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | `translation-negociar-ru/cases/titles.ru.json` | 7 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | начальник (отдела) продаж | запрещён | `translation-negociar-ru/canon-units/03-dinamica-roles-role-dynamics.md.ru.json` | 1 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | начальник (отдела) продаж | запрещён | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 3 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | менеджер по продажам (проверить исходник) | проверить | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 18 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | менеджер по продажам (проверить исходник) | проверить | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 2 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | менеджер по продажам (проверить исходник) | проверить | `translation-negociar-ru/cases/titles.ru.json` | 4 |
| gerente/director comercial → коммерческий директор | коммерческий менеджер | запрещён | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 1 |
| gerente/director comercial → коммерческий директор | коммерческий менеджер | запрещён | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 19 |
| gerente/director comercial → коммерческий директор | коммерческий менеджер | запрещён | `translation-negociar-ru/cases/titles.ru.json` | 2 |
| gerente/director de planta → директор завода | управляющий заводом | запрещён | `translation-negociar-ru/cases/accepted/batch-005.ru.json` | 7 |
| gerente/director de planta → директор завода | управляющий заводом | запрещён | `translation-negociar-ru/cases/titles.ru.json` | 5 |
| comercial (человек) → продажник | — | — | — | 0 |
| decisor → лицо, принимающее решение (ЛПР) | лицо, принимающее решения (мн. ч.) | проверить | `translation-negociar-ru/canon-units/07-tecnologia-social-social-technology.md.ru.json` | 1 |
| decisor → лицо, принимающее решение (ЛПР) | лицо, принимающее решения (мн. ч.) | проверить | `translation-negociar-ru/canon-units/11-capital-social-social-capital.md.ru.json` | 1 |
| autoridad (уважение, домен 6) → авторитет | власть (домен 6: проверить исходник autoridad/poder) | проверить (D6) | `skills/negociar-ru/data/canon/EVALS.md` | 1 |
| autoridad (уважение, домен 6) → авторитет | власть (домен 6: проверить исходник autoridad/poder) | проверить (D6) | `translation-negociar-ru/canon-units/06-autoridad-distanciamiento-authority-distancing.md.ru.json` | 16 |
| autoridad (уважение, домен 6) → авторитет | власть (домен 6: проверить исходник autoridad/poder) | проверить (D6) | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 10 |
| garra → напор (захват — только agarre) | захват при «garra» в исходнике | проверить (garra) | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 4 |
| dejarse agarrar → дать себя захватить | взять (себя) в захват | запрещён | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 2 |
| framing / encuadre / marco → рамка (в речи ведущего допустимо «фрейм») | фрейминг | запрещён | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 15 |
| framing / encuadre / marco → рамка (в речи ведущего допустимо «фрейм») | фрейминг | запрещён | `translation-negociar-ru/titles.ru.json` | 1 |
| framing / encuadre / marco → рамка (в речи ведущего допустимо «фрейм») | фрейм (только в речи ведущего) | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 9 |
| reencuadrar → сменить рамку / смена рамки | переставить рамку | запрещён | `translation-negociar-ru/canon-units/07-tecnologia-social-social-technology.md.ru.json` | 2 |
| retroalimentación (разбор ведущего, метка в случаях) → разбор | Ретроориентация | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 1 |
| lucha (раунд упражнения) → борьба | поединок | запрещён | `translation-negociar-ru/canon-units/01-fundamentos-lucha-fundamentals-management-fight.md.ru.json` | 2 |
| lucha (раунд упражнения) → борьба | поединок | запрещён | `translation-negociar-ru/canon-units/08-gestion-conflicto-conflict-management.md.ru.json` | 1 |
| clase (в «Источник») → занятие | — | — | — | 0 |
| directos → прямые эфиры | — | — | — | 0 |
| derecho a la costumbre → право привычки | право обычая | запрещён | `skills/negociar-ru/data/canon/EVALS.md` | 1 |
| derecho a la costumbre → право привычки | право обычая | запрещён | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 3 |
| derecho al olvido → право на забывание | право забыть | запрещён | `translation-negociar-ru/canon-units/08-gestion-conflicto-conflict-management.md.ru.json` | 2 |
| derecho al olvido → право на забывание | «…и на забвение» / забвение (проверить) | проверить | `skills/negociar-ru/data/canon/RECONCILIACION.md` | 1 |
| derecho al olvido → право на забывание | «…и на забвение» / забвение (проверить) | проверить | `translation-negociar-ru/canon-units/08-gestion-conflicto-conflict-management.md.ru.json` | 1 |
| captura de valor → присвоение ценности | — | — | — | 0 |
| puente de plata / de oro → золотой мост | серебряный мост | запрещён | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 1 |
| Terreno Muerto → местность смерти | мёртвая земля | запрещён | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 3 |
| salida honrosa / digna → достойный выход | почётный выход | запрещён | `translation-negociar-ru/canon-units/08-gestion-conflicto-conflict-management.md.ru.json` | 1 |
| excusa honorable → почётный повод | — | — | — | 0 |
| validación (эмоциональная) → признание | валидация | запрещён | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 2 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/canon-units/01-fundamentos-lucha-fundamentals-management-fight.md.ru.json` | 8 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/canon-units/02-imagen-mundo-picture-world.md.ru.json` | 1 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/canon-units/03-dinamica-roles-role-dynamics.md.ru.json` | 1 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 3 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/canon-units/05-ventas-complejas-complex-sales.md.ru.json` | 2 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/canon-units/07-tecnologia-social-social-technology.md.ru.json` | 4 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/canon-units/09-productividad-habitos-productivity-habits.md.ru.json` | 2 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 14 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 5 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/cases/accepted/batch-005.ru.json` | 1 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 3 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/cases/titles.ru.json` | 5 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | `translation-negociar-ru/titles.ru.json` | 1 |
| Cambio / Inversión de roles → смена ролей | перевёрнутые роли / обмен ролями / роли наоборот | проверить | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 8 |
| Cambio / Inversión de roles → смена ролей | перевёрнутые роли / обмен ролями / роли наоборот | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 4 |
| Cambio / Inversión de roles → смена ролей | обратные роли | запрещён | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 4 |
| Cambio / Inversión de roles → смена ролей | обратные роли | запрещён | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 6 |
| Cambio / Inversión de roles → смена ролей | обратные роли | запрещён | `translation-negociar-ru/cases/titles.ru.json` | 1 |
| Alumno (метка) → Ученик | Студент (метка/роль) | проверить | `translation-negociar-ru/canon-units/06-autoridad-distanciamiento-authority-distancing.md.ru.json` | 1 |
| Alumno (метка) → Ученик | Студент (метка/роль) | проверить | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 1 |
| Alumno (метка) → Ученик | Студент (метка/роль) | проверить | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 9 |
| Alumno (метка) → Ученик | Студент (метка/роль) | проверить | `translation-negociar-ru/cases/accepted/batch-005.ru.json` | 16 |
| Alumno (метка) → Ученик | Студент (метка/роль) | проверить | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 1 |
| Alumno (метка) → Ученик | Студент (метка/роль) | проверить | `translation-negociar-ru/cases/titles.ru.json` | 1 |
| Vendedor / Cliente (метки) → Продавец / Клиент | метка Покупатель/Заказчик/Торговец | проверить | `translation-negociar-ru/canon-units/05-ventas-complejas-complex-sales.md.ru.json` | 1 |
| lucha gerencial → управленческая борьба | — | — | — | 0 |
| sólido y hueco → твёрдое и пустое | прочное (проверить: sólido?) | проверить | `translation-negociar-ru/canon-units/05-ventas-complejas-complex-sales.md.ru.json` | 2 |
| sólido y hueco → твёрдое и пустое | прочное (проверить: sólido?) | проверить | `translation-negociar-ru/canon-units/11-capital-social-social-capital.md.ru.json` | 1 |
| imagen del mundo → картина мира | — | — | — | 0 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `skills/negociar-ru/SKILL.md` | 1 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/canon-units/01-fundamentos-lucha-fundamentals-management-fight.md.ru.json` | 1 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/canon-units/03-dinamica-roles-role-dynamics.md.ru.json` | 2 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/canon-units/05-ventas-complejas-complex-sales.md.ru.json` | 1 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/canon-units/06-autoridad-distanciamiento-authority-distancing.md.ru.json` | 1 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/canon-units/07-tecnologia-social-social-technology.md.ru.json` | 1 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/cases/accepted/batch-001.ru.json` | 1 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 11 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 2 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/cases/accepted/batch-005.ru.json` | 8 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | `translation-negociar-ru/cases/accepted/batch-006.ru.json` | 1 |
| acercarse al ciervo / reno → приблизиться к оленю | — | — | — | 0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | хватка | запрещён | `translation-negociar-ru/canon-units/02-imagen-mundo-picture-world.md.ru.json` | 1 |
| захват — бросок — фиксация (agarre / derribo / fijación) | зацеп | запрещён | `skills/negociar-ru/SKILL.md` | 2 |
| захват — бросок — фиксация (agarre / derribo / fijación) | зацеп | запрещён | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 2 |
| захват — бросок — фиксация (agarre / derribo / fijación) | зацеп | запрещён | `translation-negociar-ru/cases/accepted/batch-003.ru.json` | 1 |
| захват — бросок — фиксация (agarre / derribo / fijación) | свержение / сваливание | запрещён | `translation-negociar-ru/canon-units/03-dinamica-roles-role-dynamics.md.ru.json` | 1 |
| захват — бросок — фиксация (agarre / derribo / fijación) | свержение / сваливание | запрещён | `translation-negociar-ru/cases/accepted/batch-001.ru.json` | 1 |
| захват — бросок — фиксация (agarre / derribo / fijación) | свержение / сваливание | запрещён | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 1 |
| захват — бросок — фиксация (agarre / derribo / fijación) | удержание (как термин fijación?) | проверить | `translation-negociar-ru/canon-units/04-negociacion-posicional-positional-negotiation.md.ru.json` | 3 |
| захват — бросок — фиксация (agarre / derribo / fijación) | удержание (как термин fijación?) | проверить | `translation-negociar-ru/canon-units/06-autoridad-distanciamiento-authority-distancing.md.ru.json` | 3 |
| захват — бросок — фиксация (agarre / derribo / fijación) | удержание (как термин fijación?) | проверить | `translation-negociar-ru/canon-units/07-tecnologia-social-social-technology.md.ru.json` | 1 |
| suave en la forma, duro en el fondo → мягко по форме, твёрдо по сути | жёстко/твёрдо по содержанию / жёстко по сути | проверить | `skills/negociar-ru/SKILL.md` | 2 |
| suave en la forma, duro en el fondo → мягко по форме, твёрдо по сути | мягко в форме | проверить | `translation-negociar-ru/canon-units/01-fundamentos-lucha-fundamentals-management-fight.md.ru.json` | 1 |
| derecho a la ignorancia → право на незнание | право не знать / на неведение / на невежество | проверить | `skills/negociar-ru/data/canon/RECONCILIACION.md` | 1 |
| camino a la vida → дорога к жизни | — | — | — | 0 |
| fuerza de la indiferencia → сила безразличия | — | — | — | 0 |
| el «Exacto» → «Точно» | «Именно так» / «Именно» | проверить | `translation-negociar-ru/cases/accepted/batch-002.ru.json` | 2 |
| el «Exacto» → «Точно» | «Именно так» / «Именно» | проверить | `translation-negociar-ru/cases/accepted/batch-004.ru.json` | 1 |
| el «Exacto» → «Точно» | «Именно так» / «Именно» | проверить | `translation-negociar-ru/cases/accepted/batch-005.ru.json` | 3 |
| golpe en el vacío → удар в пустоту | бить/ударить по пустоте | проверить | `translation-negociar-ru/titles.ru.json` | 1 |

## Решения и замены

Замены сделаны программно (`json.load` → правка → `json.dump(ensure_ascii=False, indent=1)`), каждая
операция проверяла точное число вхождений; журнал — `changes*.json` рядом со скриптом. В изменённых
случаях пересчитан `sha256_ru` (формула `cases_tools.py import`; у неизменённых случаев хеш совпал с
прежним). Всего замен: 235.

| Решение (словарь) | Замен | Где | Примечание |
|---|---:|---|---|
| jefe → руководитель | 44 | канон 02 (4), канон 03 (9), канон 05 (6), канон 06 (13), канон 08 (2), карта заголовков канона (1), канон 07 (2), batch-002 (5), batch-004 (1), SKILL.md (1) | повествование, роли, речь ведущего; «Начальница …» → «Руководитель …» (06 #34); «Los directivos y jefes» → «Директора и руководители» (05 #7, чтобы не получить «Руководители и руководители»); «aspira a la jefatura» → «претендует на руководящую должность» (07 #19); заголовок 06 #21 «Золотое правило с руководителем» — в карте и в единице |
| jefe (обращение) → шеф | 6 | batch-001 00438826:8 ×5, batch-001 00438826:10 | «Послушайте/Слушай, руководитель» → «…, шеф» в репликах продавца (00438826:8, :10) |
| gerente comercial → коммерческий директор (в паре с director comercial — руководитель продаж) | 20 | batch-002 00438828:37 ×18, канон 04 #11 ×2 | в 00438828:37 и в примере 04 #11 gerente comercial подчинён director comercial и ведущий сам приравнивает его к jefe de ventas, поэтому роль передана как «руководитель продаж» (иначе две роли слились бы в «коммерческого директора»); фраза-пояснение «коммерческий менеджер — это начальник продаж» стала тавтологией и сокращена |
| ejecutivo comercial → менеджер по продажам | 2 | заголовки случаев 00438852:225, заголовки случаев 00438852:230 | заголовки 00438852:225, :230 (Ejecutivo comercial — не gerente) приведены к форме, принятой в 00438828:42 |
| gerente → руководитель | 62 | канон 05 #24, batch-002 00438828:38 ×17, batch-003 00438831:54 ×3, batch-003 00438831:58, batch-003 00438832:62 ×2, batch-003 00438833:67 ×16, batch-003 00438834:69 ×11, batch-004 00438836:88 ×2, batch-005 00438840:120 ×2, batch-005 00438840:123, заголовки случаев 00438926:503, заголовки случаев 00438926:504, заголовки случаев 00438926:505, batch-005 00438839:112 ×2, заголовки случаев 00438966:787 | 00438828:38, 00438831:54/58, 00438832:62, 00438833:67, 00438834:69, 00438836:88, 00438840:120/123, 00438839:112, 05 #24, заголовки 00438926:503–505, 00438966:787 |
| gerente de ventas → руководитель отдела продаж | 4 | batch-003 00438831:56 ×2, заголовки случаев 00438938:604, заголовки случаев 00438938:605 | 00438831:56; заголовки 00438938:604, :605 |
| gerente/director de planta → директор завода | 26 | batch-005 00438839:111 ×6, заголовки случаев 00438839:111, batch-005 00438839:112 ×2, заголовки случаев 00438839:112, заголовки случаев 00438852:226, заголовки случаев 00438852:232, заголовки случаев 00438855:251, batch-004 00438835:79 ×12, заголовки случаев 00438835:79 | 00438835:79 (gerente de planta был «начальником производства»), 00438839:111/112 и заголовки 00438852:226/232, 00438855:251 («управляющий заводом»); обращение участника «управляющий, думаю…» → «директор, думаю…» |
| framing → рамка (в речи ведущего — фрейм) | 15 | batch-006 00438842:143 ×6, batch-006 00438842:144, batch-006 00438842:145 ×8 | 00438842:143–145: в речи Артура/ведущего «фрейминг» → «фрейм», в речи участников и ученика → «рамка» (с согласованием: «рамка здесь ключевая»); 00438842:144 «Сделаем ещё раз фрейминг» → «Ещё раз поработаем с фреймом» |
| reencuadrar → сменить рамку | 2 | канон 07 #1, канон 07 #3 |  |
| dejarse agarrar → дать себя захватить | 2 | batch-003 00438834:70, batch-003 00438835:75 |  |
| derecho a la costumbre → право привычки | 4 | канон 04 #16 ×3, EVALS.md |  |
| derecho al olvido → право на забывание | 3 | канон 08 #3 ×2, RECONCILIACION.md |  |
| derecho a la ignorancia / al olvido (пересказ) | 1 | RECONCILIACION.md | RECONCILIACION.md T4: «право не знать и забывать» (derecho a no saber y a olvidar) → «право на незнание и на забывание» |
| puente de plata / de oro → золотой мост | 1 | канон 04 #19 |  |
| Terreno Muerto → местность смерти | 3 | канон 04 #18 ×3 |  |
| salida honrosa / digna → достойный выход | 1 | канон 08 #12 |  |
| técnica (название приёма) → приём | 20 | канон 01 #26, канон 01 #27, канон 01 #29 ×2, канон 01 #34, канон 01 #37, канон 02 #6, канон 01 #28, канон 01 #32, канон 05 #11, канон 07 #13, канон 07 #17, batch-004 00438835:78 ×4, batch-005 00438839:113, batch-006 00438841:131, batch-006 00438841:138, batch-006 00438841:140 | заголовки единиц 01 #26, #27, #29, #34, #37, 02 #6 (как в карте заголовков), 01 #28/#29/#32, 05 #11, 07 #13, #17; случаи 00438835:78, 00438839:113, 00438841:131/138/140 (с согласованием рода: «сильный приём», «обоими приёмами», «мы его разберём») |
| Cambio / Inversión de roles → смена ролей | 11 | batch-004 00438836:82, batch-004 00438836:84, batch-004 00438836:86, batch-006 00438841:132, batch-006 00438841:134, batch-006 00438841:136, batch-006 00438841:138, batch-006 00438841:140, batch-006 00438841:142, batch-004 00438836:90, заголовки случаев 00438836:90 | «с обратными ролями» → «со сменой ролей» (00438836:82/84/86, 00438841:132–142); «(Обратные роли)» → «(Смена ролей)» (00438836:90, заголовок и карта случаев) |
| мягко по форме, твёрдо по сути | 4 | SKILL.md ×2, канон 01 #10 ×2 | SKILL.md: «твёрдо по содержанию» → «твёрдо по сути» (2 места; «если содержание тоже мягкое» → «если суть тоже мягкая»); 01 #10: «Жёсткость/Мягкость в форме» → «по форме» (рядом «твёрдость по сути») |
| golpe en el vacío → удар в пустоту | 1 | карта заголовков канона 01#14 | карта заголовков 01 #14: «Бить по пустоте сильного» → «Бить в пустоту сильного» |
| interesado (D11) → заинтересованный (корыстный интерес) | 1 | канон 11 #18 | 11 #18: первое употребление «заинтересованный» (interesado) → «(корыстный интерес)» по draft-2 |
| название домена 3 (как в заголовке доменного файла) | 1 | README.md | README канона: «Ролевая динамика и сценарии власти» → «Динамика ролей и сценарии власти» (как заголовок `#` файла 03; строка проверки README «названия доменов») |
| autoridad (уважение, D6) → авторитет | 1 | карта заголовков канона 06#32 | карта заголовков 06 #32: «Потребность в «авторитарном взрослом»» → «…«авторитетном взрослом»» (как в тексте единицы) |
| **Итого** | **235** | | |

## Сводка по вариантам: до и после

«Изменение» = до − после; отрицательное значение — вариант стал утверждённой формой чаще (например,
«фрейминг» → «фрейм» в речи ведущего, «руководитель» → «шеф» в обращении).

| Термин | Вариант | Тип | До | После: исключения | После: другое значение / утв. форма | Изменение (до − после) |
|---|---|---|---:|---:|---:|---:|
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | запрещён | 66 | 16 | 0 | +50 |
| jefe → руководитель (повествование, роль, метка) | начальник производства (только jefe de planta) | проверить | 44 | 0 | 32 | +12 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | проверить | 90 | 0 | 96 | -6 |
| jefe (обращение) → шеф | «…, руководитель» в обращении | запрещён | 10 | 0 | 5 | +5 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | проверить | 211 | 2 | 123 | +86 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | проверить | 59 | 0 | 54 | +5 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | менеджер по продажам (проверить исходник) | проверить | 24 | 0 | 21 | +3 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | начальник (отдела) продаж | запрещён | 4 | 0 | 0 | +4 |
| gerente/director comercial → коммерческий директор | коммерческий менеджер | запрещён | 22 | 0 | 0 | +22 |
| gerente/director de planta → директор завода | управляющий заводом | запрещён | 12 | 0 | 0 | +12 |
| decisor → лицо, принимающее решение (ЛПР) | лицо, принимающее решения (мн. ч.) | проверить | 2 | 0 | 2 | +0 |
| autoridad (уважение, домен 6) → авторитет | власть (домен 6: проверить исходник autoridad/poder) | проверить (D6) | 27 | 1 | 26 | +0 |
| garra → напор (захват — только agarre) | захват при «garra» в исходнике | проверить (garra) | 4 | 1 | 3 | +0 |
| dejarse agarrar → дать себя захватить | взять (себя) в захват | запрещён | 2 | 0 | 0 | +2 |
| framing / encuadre / marco → рамка (в речи ведущего допустимо «фрейм») | фрейм (только в речи ведущего) | проверить | 9 | 0 | 21 | -12 |
| framing / encuadre / marco → рамка (в речи ведущего допустимо «фрейм») | фрейминг | запрещён | 16 | 0 | 1 | +15 |
| reencuadrar → сменить рамку / смена рамки | переставить рамку | запрещён | 2 | 0 | 0 | +2 |
| retroalimentación (разбор ведущего, метка в случаях) → разбор | Ретроориентация | проверить | 1 | 1 | 0 | +0 |
| lucha (раунд упражнения) → борьба | поединок | запрещён | 3 | 3 | 0 | +0 |
| derecho a la costumbre → право привычки | право обычая | запрещён | 4 | 0 | 0 | +4 |
| derecho al olvido → право на забывание | «…и на забвение» / забвение (проверить) | проверить | 2 | 0 | 1 | +1 |
| derecho al olvido → право на забывание | право забыть | запрещён | 2 | 0 | 0 | +2 |
| puente de plata / de oro → золотой мост | серебряный мост | запрещён | 1 | 0 | 0 | +1 |
| Terreno Muerto → местность смерти | мёртвая земля | запрещён | 3 | 0 | 0 | +3 |
| salida honrosa / digna → достойный выход | почётный выход | запрещён | 1 | 0 | 0 | +1 |
| validación (эмоциональная) → признание | валидация | запрещён | 2 | 2 | 0 | +0 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | проверить | 50 | 6 | 24 | +20 |
| Cambio / Inversión de roles → смена ролей | обратные роли | запрещён | 11 | 0 | 0 | +11 |
| Cambio / Inversión de roles → смена ролей | перевёрнутые роли / обмен ролями / роли наоборот | проверить | 12 | 0 | 12 | +0 |
| Alumno (метка) → Ученик | Студент (метка/роль) | проверить | 29 | 0 | 29 | +0 |
| Vendedor / Cliente (метки) → Продавец / Клиент | метка Покупатель/Заказчик/Торговец | проверить | 1 | 0 | 1 | +0 |
| sólido y hueco → твёрдое и пустое | прочное (проверить: sólido?) | проверить | 3 | 0 | 3 | +0 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | запрещён | 30 | 0 | 30 | +0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | зацеп | запрещён | 5 | 0 | 5 | +0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | свержение / сваливание | запрещён | 3 | 0 | 3 | +0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | удержание (как термин fijación?) | проверить | 7 | 0 | 7 | +0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | хватка | запрещён | 1 | 0 | 1 | +0 |
| suave en la forma, duro en el fondo → мягко по форме, твёрдо по сути | жёстко/твёрдо по содержанию / жёстко по сути | проверить | 2 | 0 | 0 | +2 |
| suave en la forma, duro en el fondo → мягко по форме, твёрдо по сути | мягко в форме | проверить | 1 | 0 | 0 | +1 |
| derecho a la ignorancia → право на незнание | право не знать / на неведение / на невежество | проверить | 1 | 0 | 0 | +1 |
| el «Exacto» → «Точно» | «Именно так» / «Именно» | проверить | 6 | 0 | 6 | +0 |
| golpe en el vacío → удар в пустоту | бить/ударить по пустоте | проверить | 1 | 0 | 0 | +1 |

## После прохода

Повторный запуск `align_terms.py scan` по тому же корпусу: 538 вхождений. Каждое разобрано
(`align_classify.py`): **32 — оставленные исключения** (перечислены ниже поштучно), остальные —
другое значение слова (шеф-повар, техник, «подстроить план», «удержание клиентов», «менеджер по
продажам» = ejecutivo и т. п.) или утверждённая форма (обращение «шеф», «власть» = poder, «фрейм» в
речи ведущего, «начальник производства» = jefe de planta). Неразобранных вхождений — 0. Запрещённых
форм в значении термина вне перечисленных исключений не осталось.

| Термин | Вариант | Исключения | Другое значение / утверждённая форма | Не классифицировано |
|---|---|---:|---:|---:|
| jefe → руководитель (повествование, роль, метка) | начальник (кроме «начальник производства») | 16 | 0 | 0 |
| jefe → руководитель (повествование, роль, метка) | начальник производства (только jefe de planta) | 0 | 32 | 0 |
| jefe → руководитель (повествование, роль, метка) | шеф (вне обращения) | 0 | 96 | 0 |
| jefe (обращение) → шеф | «…, руководитель» в обращении | 0 | 5 | 0 |
| gerente (без уточнения) → руководитель | менеджер (проверить исходник: gerente?) | 2 | 123 | 0 |
| gerente (без уточнения) → руководитель | управляющий (кроме «…заводом»; проверить исходник) | 0 | 54 | 0 |
| gerente/jefe de ventas → руководитель отдела продаж / руководитель продаж | менеджер по продажам (проверить исходник) | 0 | 21 | 0 |
| decisor → лицо, принимающее решение (ЛПР) | лицо, принимающее решения (мн. ч.) | 0 | 2 | 0 |
| autoridad (уважение, домен 6) → авторитет | власть (домен 6: проверить исходник autoridad/poder) | 1 | 26 | 0 |
| garra → напор (захват — только agarre) | захват при «garra» в исходнике | 1 | 3 | 0 |
| framing / encuadre / marco → рамка (в речи ведущего допустимо «фрейм») | фрейм (только в речи ведущего) | 0 | 21 | 0 |
| framing / encuadre / marco → рамка (в речи ведущего допустимо «фрейм») | фрейминг | 0 | 1 | 0 |
| retroalimentación (разбор ведущего, метка в случаях) → разбор | Ретроориентация | 1 | 0 | 0 |
| lucha (раунд упражнения) → борьба | поединок | 3 | 0 | 0 |
| derecho al olvido → право на забывание | «…и на забвение» / забвение (проверить) | 0 | 1 | 0 |
| validación (эмоциональная) → признание | валидация | 2 | 0 | 0 |
| técnica (название приёма) → приём | техника (проверить: название приёма?) | 6 | 24 | 0 |
| Cambio / Inversión de roles → смена ролей | перевёрнутые роли / обмен ролями / роли наоборот | 0 | 12 | 0 |
| Alumno (метка) → Ученик | Студент (метка/роль) | 0 | 29 | 0 |
| Vendedor / Cliente (метки) → Продавец / Клиент | метка Покупатель/Заказчик/Торговец | 0 | 1 | 0 |
| sólido y hueco → твёрдое и пустое | прочное (проверить: sólido?) | 0 | 3 | 0 |
| unirse a la imagen del mundo → присоединиться к картине мира | подстроиться | 0 | 30 | 0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | зацеп | 0 | 5 | 0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | свержение / сваливание | 0 | 3 | 0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | удержание (как термин fijación?) | 0 | 7 | 0 |
| захват — бросок — фиксация (agarre / derribo / fijación) | хватка | 0 | 1 | 0 |
| el «Exacto» → «Точно» | «Именно так» / «Именно» | 0 | 6 | 0 |

### Оставленные исключения (поштучно)

| # | Файл | Место | Вариант | Контекст | Причина |
|---:|---|---|---|---|---|
| 1 | канон 05 | #24 body_ru | начальник | …личку, и один из них признаётся: *«Моя настоящая проблема — **начальник**: он бумер и не понимает цифровую среду»*. Автор находит конкретную… | 05 #24: цитата подписчика «Моя настоящая проблема — начальник…» и название его видео — живая речь в кавычках |
| 2 | канон 05 | #24 body_ru | начальник | … и сильную боль и снимает видео *«Как выстроить отношения с **начальником** старой закалки»*, которое становится вирусным, потому что отклик… | 05 #24: цитата подписчика «Моя настоящая проблема — начальник…» и название его видео — живая речь в кавычках |
| 3 | канон 06 | #11 body_ru | начальник | …, у старожилов появится недоверие (*«этот выпендривается, а **начальник** ещё и приближает его»*), и авторитет лидера в глазах группы будет … | 06 #11: мысль старожилов в кавычках («этот выпендривается, а начальник ещё и приближает его») — разговорная речь |
| 4 | канон 06 | #22 body_ru | начальник | …о нападение тебя не задело: *«Да, Carlos, в этой компании у **начальников** тоже есть начальники, и с нас тоже требуют результатов. Кстати, … | 06 #22: реплика-образец в кавычках («у начальников тоже есть начальники») — разговорный оборот |
| 5 | канон 06 | #22 body_ru | начальник | …дело: *«Да, Carlos, в этой компании у начальников тоже есть **начальники**, и с нас тоже требуют результатов. Кстати, раз тебя так интересую… | 06 #22: реплика-образец в кавычках («у начальников тоже есть начальники») — разговорный оборот |
| 6 | канон 07 | #23 body_ru | начальник | …я:* Сотрудник дарит руководителю книгу *«Как стать лучшим **начальником**»*. Руководитель с враждебностью отвергает подарок, потому что скры… | 07 #23: цитаты в кавычках — название книги «Как стать лучшим начальником», скрытый посыл «ты плохой начальник», реплика сотрудника |
| 7 | канон 07 | #23 body_ru | начальник | …ю отвергает подарок, потому что скрытый посыл — *«ты плохой **начальник**»*. Если сотрудник подойдёт и скажет: *«Шеф, вы отличный лидер, а э… | 07 #23: цитаты в кавычках — название книги «Как стать лучшим начальником», скрытый посыл «ты плохой начальник», реплика сотрудника |
| 8 | канон 07 | #23 body_ru | начальник | …умент должен быть таким: *«Зависеть от одного-единственного **начальника** — самое нестабильное положение, какое только бывает. Единственный… | 07 #23: цитаты в кавычках — название книги «Как стать лучшим начальником», скрытый посыл «ты плохой начальник», реплика сотрудника |
| 9 | batch-002 | 00438827:33 body_ru | начальник | …том, что есть... правки без объяснений, ладно? Со стороны **начальника** дизайнера. Что привлекает моё внимание здесь — это раздутое, огромн… | 00438827:33: живая речь участников разбора (Участники C, D) |
| 10 | batch-002 | 00438827:33 body_ru | начальник | …ь одну-две, либо не объясняешь ни одной. И думаю, что здесь **начальник** был более разумным, поскольку он потратил время, чтобы их исправит… | 00438827:33: живая речь участников разбора (Участники C, D) |
| 11 | batch-002 | 00438827:33 body_ru | начальник | …м случае — как ты анализируешь власть, которая есть у этого **начальника**? Как ты к ней подходишь? Вот мой вопрос.» ---… | 00438827:33: живая речь участников разбора (Участники C, D) |
| 12 | batch-002 | 00438828:35 body_ru | начальник | …Участник A):** «Следи за тем, как ты обращаешься со своим **начальником**, может, перестанешь терять деньги в этой операции.» - **Директор п… | 00438828:35: реплика участника в ролевой игре («Следи за тем, как ты обращаешься со своим начальником») |
| 13 | batch-003 | 00438831:54 body_ru | начальник | …торые возникали неожиданно. Например, когда я был стажёром, **начальник** не смог прийти на встречу с другими клиентами. И тогда мне пришлос… | 00438831:54: живая речь участников собеседования («когда я был стажёром, начальник…», «твой прошлый начальник») |
| 14 | batch-003 | 00438831:54 body_ru | начальник | …ктами" - **Участник A:** "Как бы тебя описал твой прошлый **начальник**?" - **Участник B:** "Какова цель? В чём вызов на этой должности?" - … | 00438831:54: живая речь участников собеседования («когда я был стажёром, начальник…», «твой прошлый начальник») |
| 15 | batch-004 | 00438839:98 body_ru | начальник | …, и поймите, что безопасность, благополучие и координация с **начальниками**, чтобы праздники состоялись. Если нет — не знаю, какой другой в… | 00438839:98: живая речь участника («координация с начальниками») |
| 16 | SKILL.md | L309 line | начальник | …… | SKILL.md L309: цитата запроса пользователя «будь моим начальником» (sé mi jefe) — разговорная речь |
| 17 | канон 05 | #16 body_ru | менеджер | …ы экономию на производстве?»*. CEO сразу отмечает, что этот **менеджер** видит всю доску бизнеса целиком. - **См. также:** [[El principio de… | 05 #16: «этот менеджер» — отсылка к «операционному менеджеру» (gerente de operaciones, уточнённая должность, в таблице draft-2 нет) — вопрос Дмитрию |
| 18 | batch-003 | 00438833:67 body_ru | менеджер | …ют неуважение, вплоть до, вплоть до руководителя, вплоть до **менеджера**, если он уже начал бороться, можно воспитывать людей не проявлять … | 00438833:67: перечисление синонимов в речи ведущего «вплоть до руководителя, вплоть до менеджера» (hasta el jefe, hasta el gerente) — при замене получится повтор |
| 19 | канон 06 | #9 body_ru | власть | …роактивность или «опережающий контроль», чтобы узурпировать **власть** руководителя или увильнуть от работы. Это проявляется двумя способами… | 06 #9: «узурпировать власть руководителя» (usurpar la autoridad) — по корневому словарю usurpación de autoridad → «узурпация власти»; конфликт с draft-2 «autoridad → авторитет» — вопрос Дмитрию |
| 20 | batch-003 | 00438835:75 body_ru | захват при «garra» в исходнике | …«с пустым животом — как будет работать», и ты делаешь этот **захват**, это этот захват человека, но вопрос такой открытый, что вопрос хороши… | 00438835:75: «haces esta garra, es esta garra la persona» — garra здесь в смысле agarre (захват собеседника), не «напор» |
| 21 | batch-006 | 00438841:131 body_ru | Ретроориентация | … снизить зарплату вам." - **Ведущий:** "Хорошо. Э... Ок. **Ретроориентация**. Попробуй... Это танец. Это борьба. Захватить... В борьбе мы за… | 00438841:131: оговорка ведущего «Retroorientación» сохранена как речь (строка проверки 00438841:131 остаётся open) |
| 22 | канон 01 | #23 body_ru | поединок | …ьщиках:* Тридцать лет двое мужчин соперничают. В решающем **поединке** первый обезоруживает второго. Противник на земле, победа полная. В от… | в исходнике duelo («поединок»), а не lucha — правило «lucha → борьба» не применимо; вопрос Дмитрию про duelo |
| 23 | канон 01 | #32 body_ru | поединок | …ировать разрешение конфликтов на симуляциях (управленческих **поединках**), чтобы конструктивные ответы выходили естественно и без выброса а… | в исходнике duelo («поединок»), а не lucha — правило «lucha → борьба» не применимо; вопрос Дмитрию про duelo |
| 24 | канон 08 | #8 body_ru | поединок | …треть, вспыхнешь ли ты. - **Как исполнять:** Выиграй первый **поединок** ясно и с явным перевесом — так устанавливаются границы, чтобы потом… | в исходнике duelo («поединок»), а не lucha — правило «lucha → борьба» не применимо; вопрос Дмитрию про duelo |
| 25 | batch-006 | 00438841:134 body_ru | валидация | …елей с этой новой страницей. И у нас уже действительно есть **валидация** с пользователями." - **Участник B:** "И результат был впечатляющим… | 00438841:134: «валидация с пользователями» — продуктовая проверка гипотезы (validación con usuarios), жаргон участника, повторённый ведущим как цитата; не эмоциональная validación |
| 26 | batch-006 | 00438841:134 body_ru | валидация | …фект важен. Это повлияет на производительность. У нас есть **валидация** пользователей. Всё хорошо. Эти фразы не слышат, если мы не занимаем… | 00438841:134: «валидация с пользователями» — продуктовая проверка гипотезы (validación con usuarios), жаргон участника, повторённый ведущим как цитата; не эмоциональная validación |
| 27 | канон 04 | #20 body_ru | техника | …ием?»*. 2. **Спаси лицо руководителя, отделив личность от **техники**:** Исходи из того, что его намерения прекрасны, но административный пр… | 04 #20: «отделив личность от техники» (separando identidad de técnica) — техническая сторона решения, не название приёма |
| 28 | канон 09 | #19 title_ru | техника | …е в абсолютных числах; и настрой побеждает **технику**… | 09 #19: «настрой побеждает технику» (la mentalidad vence a la técnica) — техника как навык исполнения, не название приёма |
| 29 | канон 09 | #19 body_ru | техника | …* (настрой на решение), ценнее, чем *ноу-хау*: у кого есть **техника**, но нет настроя, тот останавливается на первой же кочке; у кого есть … | 09 #19: «настрой побеждает технику» (la mentalidad vence a la técnica) — техника как навык исполнения, не название приёма |
| 30 | карта заголовков канона | 09#19 title_ru | техника | …е в абсолютных числах; а мышление побеждает **технику**… | 09 #19: «настрой побеждает технику» (la mentalidad vence a la técnica) — техника как навык исполнения, не название приёма |
| 31 | batch-003 | 00438832:59 body_ru | техника | …ажем, слушай, если хочешь ответственность — бери. На уровне **техники** хорошо. На уровне борьбы во второй борьбе тоже довольно хорошо. Тема… | «на уровне техники … на уровне борьбы» (a nivel de técnica) — оценка исполнения в разборе, не название приёма |
| 32 | batch-004 | 00438835:77 body_ru | техника | …ую безопасность при подаче заявления и так далее, на уровне **техники** и на уровне борьбы всё безупречно, всё безупречно, поздравляю, итак,… | «на уровне техники … на уровне борьбы» (a nivel de técnica) — оценка исполнения в разборе, не название приёма |

### Другое значение слова или утверждённая форма (по группам)

- утверждённая форма: jefe de planta → «начальник производства» (00438828:35) — 32: batch-002 00438828:35 ×32
- обращение «шеф» в прямой речи — утверждённая форма для jefe в обращении — 52: канон 01 #6, канон 01 #7 ×2, канон 01 #10, канон 01 #15, канон 01 #33, канон 01 #38, канон 02 #7, канон 02 #13, канон 03 #1 ×2, канон 03 #2, канон 03 #6 ×2, канон 03 #17, канон 03 #18, канон 03 #23, канон 04 #6, канон 04 #7 ×2, канон 04 #11, канон 04 #20, канон 04 #25, канон 06 #21 ×2, канон 06 #26, канон 06 #34, канон 07 #20, канон 07 #23, batch-001 00438826:10, batch-001 00438826:8 ×5, batch-002 00438827:26 ×2, batch-002 00438827:28, batch-002 00438827:29, batch-002 00438827:31, batch-002 00438829:45 ×2, batch-003 00438833:65 ×4, batch-003 00438833:67, batch-003 00438833:68 ×2, batch-003 00438834:69, EVALS.md L50, EVALS.md L106
- су-шеф (sous-chef) — другое значение — 2: канон 01 #6 ×2
- шеф-повар (chef) — другое значение — 30: канон 01 #6, batch-002 00438830:49 ×18, batch-004 00438839:100 ×3, batch-004 00438839:99 ×3, batch-006 00438841:139 ×2, batch-006 00438841:140, заголовки случаев 00438839:99, заголовки случаев 00438839:100
- шеф = шеф-повар (chef) в случаях о ресторане — другое значение — 12: batch-004 00438839:100 ×2, batch-004 00438839:99 ×7, batch-006 00438841:139, batch-006 00438841:140 ×2
- не обращение: название роли в блоке «Роли» («руководитель» в кавычках после метки) — 2: batch-002 00438828:36, batch-002 00438828:38
- не обращение: «руководитель» как подлежащее/приложение в речи ведущего («Может быть, руководитель, скажем, …», «…, руководитель, обеспокоенный …») — 3: batch-003 00438832:59, batch-003 00438833:65 ×2
- другая должность: менеджер по продажам (ejecutivo comercial/de ventas), по работе с клиентами (ejecutivo/gerente de cuentas), по качеству, по маркетингу — 53: канон 01 #16, канон 05 #17 ×2, batch-001 00438826:12 ×11, batch-001 00438827:25 ×4, batch-002 00438828:42 ×17, batch-004 00438835:78 ×10, заголовки случаев 00438827:25, заголовки случаев 00438835:78, заголовки случаев 00438846:187, заголовки случаев 00438847:199, заголовки случаев 00438847:200, заголовки случаев 00438851:220, заголовки случаев 00438852:225, заголовки случаев 00438852:230
- в исходнике англицизм manager (не gerente) — 12: канон 01 #20, канон 03 #3, канон 03 #21 ×2, канон 03 #23 ×2, batch-001 00438826:12 ×3, batch-003 00438834:72 ×2, batch-004 00438839:100
- менеджер среднего звена (в исходнике manager / mandos medios) — 2: канон 03 #23, batch-006 00438840:127
- утверждённая/уточнённая должность: финансовый менеджер (gerente financiero), нанимающий менеджер (hiring manager), операционный менеджер (gerente de operaciones) — 43: канон 04 #18 ×2, канон 05 #16, batch-002 00438830:47 ×15, batch-003 00438831:51 ×3, batch-003 00438831:52, batch-003 00438831:53 ×6, batch-003 00438831:54, batch-003 00438831:55 ×5, batch-003 00438831:56, batch-003 00438831:57 ×5, batch-003 00438831:58, заголовки случаев 00438853:240, заголовки случаев 00438853:241
- топ-менеджер / офис-менеджер (в исходнике top manager / office manager) — 6: канон 05 #5, канон 09 #28, канон 11 #10, заголовки случаев 00438950:689, заголовки случаев 00438959:723, заголовки случаев 00438959:724
- сокращение должности ejecutiva/ejecutivo («менеджер по работе с клиентами», «менеджер по продажам»), не gerente — 7: batch-001 00438827:25 ×5, batch-002 00438828:42 ×2
- gerente del banco → «управляющий банка» (уточнённая должность) — вопрос Дмитрию — 1: канон 01 #15
- gestores → «управляющие» (не gerente) — 1: batch-002 00438828:43
- gerente de sucursal → «управляющий отделением» (уточнённая должность, в таблице draft-2 нет) — вопрос Дмитрию — 20: batch-003 00438832:64 ×12, batch-006 00438841:141 ×5, batch-006 00438841:142 ×2, заголовки случаев 00438832:64
- gerente de tienda → «управляющий магазином» (уточнённая должность) — вопрос Дмитрию — 18: batch-004 00438836:87 ×17, заголовки случаев 00438851:222
- gerente del hotel → «управляющий отелем» (уточнённая должность) — вопрос Дмитрию — 11: batch-004 00438839:100 ×2, batch-004 00438839:99 ×7, заголовки случаев 00438839:99, заголовки случаев 00438839:100
- gerente de zona → «территориальный управляющий» (уточнённая должность) — вопрос Дмитрию — 2: заголовки случаев 00438853:244, заголовки случаев 00438853:245
- «управляющий принцип» — другое значение — 1: SKILL.md L244
- менеджер по продажам = ejecutivo/ejecutiva comercial / de ventas (не gerente/jefe de ventas) — 21: batch-002 00438828:42 ×17, заголовки случаев 00438847:199, заголовки случаев 00438847:200, заголовки случаев 00438852:225, заголовки случаев 00438852:230
- форма мн. ч. утверждённого термина «лицо, принимающее решение» — 2: канон 07 #12, канон 11 #13
- в исходнике poder → «власть» (утверждённая форма для poder) — 25: канон 06 preamble, канон 06 #6, канон 06 #17, канон 06 #20 ×5, канон 06 #21 ×2, канон 06 #25 ×2, канон 06 #29 ×2, batch-002 00438827:33 ×10, EVALS.md L103
- «властным «Заткнись и делай!»» — autoritario (авторитарный), не autoridad — 1: канон 06 #33
- в исходнике agarre / dejarse agarrar → «захват» (утверждённая форма) — 3: batch-003 00438835:75 ×3
- первое употребление в скобках: «Рамка (фрейминг) как карта сражения» — разрешено словарём — 1: карта заголовков канона 07#1
- речь ведущего (Артур) — «фрейм» допустим словарём — 12: batch-006 00438842:143 ×12
- речь ведущего (Контекст/Ведущий) — «фрейм» допустим словарём — 9: batch-006 00438842:144, batch-006 00438842:145 ×8
- «забвение растворит вопрос» (el olvido) — обычное слово, не термин — 1: канон 08 #3
- техника безопасности — другое значение — 1: канон 03 #9
- техник (человек) — другое значение — 21: канон 04 #4 ×2, канон 07 #3 ×2, batch-003 00438832:61 ×13, заголовки случаев 00438847:203, заголовки случаев 00438847:204, заголовки случаев 00438853:236, заголовки случаев 00438853:237
- техника = оборудование — другое значение — 2: канон 05 #10, заголовки случаев 00438979:911
- «поменяться ролями» — глагольный оборот (указание ведущего), не метка «Смена ролей» — 12: batch-002 00438828:34, batch-002 00438828:35, batch-002 00438828:36, batch-002 00438828:38, batch-002 00438828:39, batch-002 00438828:40, batch-002 00438828:43, batch-002 00438829:45, batch-006 00438840:128, batch-006 00438840:129, batch-006 00438840:130, batch-006 00438842:150
- студенты (estudiantes / alumnos в учебном заведении) — не метка говорящего — 29: канон 06 #27, batch-003 00438831:57, batch-004 00438835:77 ×9, batch-005 00438839:101 ×8, batch-005 00438839:102 ×8, batch-006 00438842:150, заголовки случаев 00438843:162
- «Покупатель не знает, чего хочет» — пункт списка (comprador), не метка говорящего — 1: канон 05 #10
- «прочный» в обычном значении (прочное бизнес-обоснование, фильтры на прочность) — не термин «твёрдое и пустое» — 3: канон 05 #10, канон 05 #17, канон 11 #5
- «подстроить(ся)» = adaptar / ajustar (подстроить план, процесс, дизайн) — не перевод unirse a la imagen del mundo — 30: канон 01 #43, канон 03 #21, канон 03 #26, канон 05 #24, канон 06 #23, канон 07 #2, batch-001 00438826:17, batch-003 00438832:60, batch-003 00438833:66 ×8, batch-003 00438833:68, batch-003 00438834:74, batch-004 00438835:76, batch-004 00438836:81, batch-005 00438839:106 ×2, batch-005 00438839:107 ×3, batch-005 00438839:108 ×2, batch-005 00438839:114, batch-006 00438841:142, SKILL.md L196
- «деловая хватка» — идиома (деловое чутьё), не agarre — 1: канон 02 #9
- «зацепить(ся)» = enganchar / ухватиться (зацепить аудиторию, не за что зацепиться) — не термин agarre — 5: batch-002 00438828:39, batch-002 00438829:45, batch-003 00438831:56, SKILL.md L194, SKILL.md L195
- «сваливать (вину, проблему)» — обычное значение, не derribo — 3: канон 03 #4, batch-001 00438827:23, batch-004 00438835:78
- «удержание» клиентов / талантов / платежа (retención) — не термин fijación — 7: канон 04 #10, канон 04 #20, канон 04 #24, канон 06 #7, канон 06 #27, канон 06 #33, канон 07 #6
- «именно так / именно такая» в обычной речи (así es, es exactamente…) — не маркер «Точно» (все «Exacto»-маркеры переданы «Точно») — 6: batch-002 00438827:26, batch-002 00438828:42, batch-004 00438837:91, batch-005 00438839:103 ×2, batch-005 00438839:106

## Сборка и статусы

- `python3 translation-negociar-ru/tools/canon_tools.py assemble` → 10 файлов собраны; `python3 translation-negociar-ru/tools/canon_tools.py check` →
  «Принципов: 275/275; файлов: 10; проблем: 0» (после правок и после повышения статусов).
- Заголовки канона менялись только в `translation-negociar-ru/titles.ru.json` (06 #21, 06 #32, 01 #14); ссылки
  `[[…]]` разрешаются сборщиком; заголовки внутри каждого файла канона уникальны (проверено скриптом).
- `python3 translation-negociar-ru/tools/cases_tools.py titles-import translation-negociar-ru/cases/titles.ru.json` → «Заголовки приняты: 1019».
- `python3 translation-negociar-ru/tools/cases_tools.py assemble` → «casos.json: записей 1019; полный перевод 150; только
  заголовок 868; изъято 1; без перевода 0»; `translation_status` у 150 принятых случаев — `terminology_checked`.
- Статусы: 274 единицы канона (кроме изъятой 05 #8) и 150 принятых случаев — `terminology_checked`,
  `glossary_version` = `draft-2` (у канона — на уровне файла).

## Заметки проверки (`translation-review.jsonl`)

Закрыто 9 строк, решённых словарём: EVALS «Entendido, jefe» (обращение «шеф»), EVALS «Derecho a la
Costumbre» («право привычки»), README «названия доменов» («Авторитет…», таблица доменов сверена),
00438839:103 «buena garra» («напор»), batch-002 «gerente…» (единый перевод gerente), 11 #18
«interesados», 06 #32 «adulto autoritario» («авторитетный»), 00438826:15 «Arturo (Ponente)»
(«Артур (ведущий)»), 03 #1 «clase → занятие». Остальные 149 строк — `open` (ошибки и спорное
содержание источника, ASR, вопросы вне словаря). Частично решены словарём, но оставлены открытыми,
потому что в них есть нерешённая часть: 00438839:111 (ausentismo «невыходы»), 00438840:129
(inventario «учёт запасов»), 00438841:131 (префиксы «Продавец / Клиент», медсёстры),
00438842:146 (coordinadora de sostenibilidad, supervisor regional, gerente de sucursal), batch-002
«(общее)» (пунктуация).

## Вне области прохода

- `skills/negociar-ru/data/canon/00-ARQUITECTURA.md` не входил в список файлов: в нём остались
  «почётный выход» (стр. 129) и «Рамка и фрейминг / Framing» (стр. 69).
- `translation-negociar-ru/canon-units/*.ru.parts/` и `translation-negociar-ru/cases/work/` содержат текст до прохода
  (статус `machine_draft`, словарь `draft-1`): повторный `canon_tools.py merge-parts` или
  `cases_tools.py pack` перезапишет выровненные файлы.
- `cases_tools.py status` пишет `corpus_status: machine_draft` жёстко (не по статусам случаев);
  команда в этом проходе не запускалась.
- `units[].title_ru` в `canon-units` во многом расходятся с картой `titles.ru.json` (сборщик берёт
  карту); в проходе в них правились только термины.
- «начальство» (собирательное) в таблице draft-2 не упомянуто и не менялось.

## Вопросы к Дмитрию

1. **gerente comercial при director comercial** (00438828:37, 04 #11): роль передана как
   «руководитель продаж», а не «коммерческий директор» по таблице — иначе две роли сливаются.
   Подтвердить или выбрать другое (например, «руководитель коммерческого отдела»).
2. **Уточнённые gerente вне таблицы**: de sucursal → «управляющий отделением», del hotel →
   «управляющий отелем», de tienda → «управляющий магазином», de zona → «территориальный
   управляющий», del banco → «управляющий банка», de operaciones → «операционный менеджер»
   (05 #16), de marketing → «менеджер по маркетингу» (заголовок 00438846:187), de cuentas →
   «менеджер по ключевым клиентам / по работе с клиентами». Оставить или привести к «руководителю …»?
3. **autoridad**: в 06 #9 оставлено «узурпировать власть» по корневому термину usurpación de
   autoridad → «узурпация власти» — конфликт с draft-2 «autoridad → авторитет»? Вне D6 autoridad в
   смысле формальной власти/полномочий (D3, D7, D8, 00438836:82) оставлено «власть / полномочия».
4. **duelo**: 01 #23, 01 #32 («управленческих поединках»), 08 #8 — в исходнике duelo, оставлено
   «поединок». Нужна ли для учебных раундов (01 #32) «борьба»?
5. **Прямая речь с «начальник»** (16 исключений): оставлено в репликах участников, цитатах и
   образцах реплик; в речи ведущего, повествовании и метках заменено. Подтвердить политику.
6. **«валидация с пользователями»** (00438841:134, продуктовая проверка) — оставить или «проверка
   с пользователями»?
7. 00438833:67 «вплоть до руководителя, вплоть до менеджера» (перечисление jefe/gerente) —
   оставить или сократить до одного слова.
8. 05 #7 «Los directivos y jefes» → «Директора и руководители» — подтвердить.
9. RECONCILIACION T4: «право не знать и забывать» (derecho a no saber y a olvidar) приведено к
   терминам «право на незнание и на забывание» — подтвердить.
10. Править ли `00-ARQUITECTURA.md` («почётный выход», «Рамка и фрейминг»)?
11. «Ретроориентация» (оговорка ведущего, 00438841:131) — оставить как речь или «Разбор»?
12. Строки проверки, решённые частично (см. выше), — нужны решения по ausentismo, inventario,
    префиксам «Продавец / Клиент», ролям медсестёр и координаторов.


## Решения по вопросам прохода (Ильич, 2026-10-19; рабочие, на утверждение Дмитрию)

1. gerente comercial под director comercial — «руководитель продаж»: да.
2. Уточнённые должности gerente — оставить устойчивые русские названия (управляющий отделением/отелем/магазином, операционный менеджер, менеджер по маркетингу, менеджер по работе с клиентами); внесено в словарь.
3. «Узурпация власти» — оставить (корневой термин); правило «autoridad → авторитет» уточнено в словаре.
4. duelo — оставить «поединок».
5. «Начальник» в живой речи и цитатах — допустим; в речи ведущего, повествовании и метках — «руководитель». Правило в словаре.
6. «Валидация с пользователями» (00438841:134) — заменено на «проверка».
7. «Вплоть до руководителя, вплоть до менеджера» (00438833:67) — оставить как речь.
8. 05 #7 «Директора и руководители» — да.
9. RECONCILIACION T4 «право на незнание и на забывание» — да.
10. 00-ARQUITECTURA — исправлено: «Рамка (фрейминг)», «достойный выход».
11. «Ретроориентация» (00438841:131) — оставить как оговорку в речи.
12. ausentismo → «невыходы на работу», inventario → «учёт запасов», метки «Продавец / Клиент» и названия ролей медсестёр и координаторов — оставить как переведены.

Каталоги частей переводчиков (`canon-units/*.ru.parts/`, `cases/work/`) с текстом до прохода удалены, чтобы повторный `merge-parts` или `pack` не перезаписал выровненные файлы.
