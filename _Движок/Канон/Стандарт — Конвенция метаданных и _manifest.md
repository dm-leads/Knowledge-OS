---
type: standard
status: active
description: |
  Обязательный YAML-frontmatter знаниевых документов Knowledge-OS, привязка review_cycle к типу,
  значения статусов, опциональный gitlab_url, soft mirror и структура _manifest.md как навигации контура.
author: Дмитрий + Claude (AI-assistant)
created: 2026-06-28
updated: 2026-06-28
review_cycle: 3m
last_reviewed: 2026-06-28
tags:
  - стандарт
  - метаданные
  - manifest
  - канон
---

# Стандарт — Конвенция метаданных и _manifest

## Краткая суть

Каждый знаниевый документ начинается с обязательного YAML-frontmatter. Поля едины для всех проектов.
Навигация контура — в `_manifest.md`, а не в README.

## 1. Обязательный frontmatter

```yaml
---
тип-размещения: <slug>      # area | project | resource_kind — для документов проекта; движок-канон опускает
type: <id из закрытого списка>
status: draft | active | deprecated | superseded
description: |
  <1–2 предложения: что это, зачем, для кого>
author: <Имя>
created: YYYY-MM-DD          # неизменна
updated: YYYY-MM-DD          # дата последней содержательной правки
review_cycle: 6m | 3m | never
last_reviewed: YYYY-MM-DD    # дата осознанного пересмотра (≠ updated)
gitlab_url: <url>            # ОПЦИОНАЛЬНО — только если контур привязан к GitLab
tags:
  - <тег>
---
```

- Перед закрывающим `---` — **пустая строка** (критично для рендера).
- Брать из шаблона в `Шаблоны/` (`Шаблон — <Тип>.md`), не с белого листа.
- Поле размещения (`area`/`project`/`resource_kind`) обязательно для документов **проекта**; документы
  самого движка (`_Движок/Канон`) его опускают.

## 2. review_cycle ← тип

- **6m:** policy, methodology, playbook, reference, guide, bootstrap, template.
- **3m:** standard, workflow, runbook, instruction.
- **never:** analytics, retrospective, postmortem, audit, research, decision (снапшоты).

При `review_cycle: never` поле `last_reviewed` = `created`.

## 3. Статусы

- `draft` — в работе, ещё не канон.
- `active` — авторитетный источник.
- `deprecated` — устарел, оставлен для истории.
- `superseded` — заменён другим (добавить `superseded_by: <путь>`).
- **Не `archived`** — архивирование делается механизмом хранилища, а не статусом.

## 4. gitlab_url и soft mirror

- `gitlab_url` — опционален; заполняется, когда контур реально лежит в GitLab.
- `mirrors:` — мягкое зеркало при ограничении доступа (канон в источнике, тут — вторичная копия):

```yaml
mirrors:
  - source: <путь-к-источнику>
    sections: [<Раздел>]
    reason: <почему зеркало, а не ссылка>
```

## 5. `_manifest.md` — навигация контура

В корне каждого контура. Содержит: назначение контура, что сюда заходит / что **не** заходит
(кросс-ссылки на соседей), каталог ключевых документов (ссылки), владельца. Frontmatter манифеста:
`type: manifest`, поле `repo_type: area | project | resource | tool | engine`.

# Связанные документы

- [Стандарт — Конвенция типов документов](<Стандарт — Конвенция типов документов.md>)
- [Стандарт — Конвенция ссылок и навигации](<Стандарт — Конвенция ссылок и навигации.md>)
- [Стандарт — Память изменений и System Docs](<Стандарт — Память изменений и System Docs.md>)
