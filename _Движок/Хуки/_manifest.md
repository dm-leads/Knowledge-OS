---
type: manifest
repo_type: engine
status: active
description: |
  Хуки Claude Code устройства — исходник в каноне. Папка `~/.claude/hooks` — ссылка сюда; подключение хука —
  запись в `~/.claude/settings.json` с путём `C:/Users/<пользователь>/.claude/hooks/<файл>`.
author: Дмитрий + Claude (AI-assistant)
created: 2026-09-28
updated: 2026-09-28
review_cycle: 6m
last_reviewed: 2026-09-28
tags:
  - manifest
  - хуки
---

# Хуки — _manifest

Хук — запрет в коде: промпт нарушается, хук нет. Исходник один, здесь; в `~/.claude/hooks` — ссылка-переход
(Бутстрап канона, шаг 3б), поэтому правка хука сразу действует на устройстве, а на новом устройстве защита не
теряется.

| Хук | Событие | Что запрещает | Тест |
|---|---|---|---|
| `block_last30days_publish.py` | `PreToolUse`, Bash, если в команде `last30days` | Выгрузку отчёта навыка last30days на внешний хостинг (`--publish`, `--publish-html`): исследования наружу — только по явному решению владельца | `test_block_last30days_publish.py` |

Подключение в `~/.claude/settings.json` (раздел `hooks`):

```json
"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
  "command": "PYTHONIOENCODING=utf-8 python \"C:/Users/<пользователь>/.claude/hooks/block_last30days_publish.py\"",
  "if": "Bash(*last30days*)", "timeout": 15, "statusMessage": "Проверка: last30days не публикует наружу"}]}]
```

Тесты: из этой папки `py -3 -m pytest -q -p no:cacheprovider`.

# Связанные документы

- [Бутстрап — Настройка движка Knowledge-OS](<../Сценарии/Бутстрап — Настройка движка Knowledge-OS.md>)
- [Справочник — Сторонние навыки](<../../Экосистема инструментов/03 — Ресурсы/Оценки инструментов/Справочник — Сторонние навыки (источники, версии, установка).md>)
