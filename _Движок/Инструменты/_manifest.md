---
type: manifest
repo_type: engine
status: active
description: |
  Инструменты движка Knowledge-OS — скрипты, которые обслуживают сам канон (выпуск пакетов в проекты).
author: Дмитрий + Claude (AI-assistant)
created: 2026-09-28
updated: 2026-09-28
review_cycle: 6m
last_reviewed: 2026-09-28
tags:
  - manifest
  - инструменты
---

# Инструменты движка — _manifest

| Инструмент | Что делает | Тесты |
|---|---|---|
| `kos_release.py` | Выпуск пакета канона в проект с паспортом и проверка выпуска: правки руками, отставание от канона ([ADR-0003](<../0 — System Docs/ADR-log/0003-code-systems-source-in-canon-releases-in-projects.md>)) | `test_kos_release.py` — из этой папки `py -3 -m pytest test_kos_release.py` |
| `kos_connect.py` | Подключение проекта к канону: ссылка `.kos`, блок «Канон Knowledge-OS» в `CLAUDE.md`, исключение ссылки из git ([ADR-0004](<../0 — System Docs/ADR-log/0004-connect-any-project-by-link-and-passport-block.md>)) | `test_kos_connect.py` |

Инструменты для проектов (API, сервисы, MCP) — не здесь, а в реестре
[Экосистема инструментов](<../../Экосистема инструментов/_manifest.md>).
