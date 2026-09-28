---
area: ekosistema-instrumentov
type: reference
status: active
description: |
  Оценка gstack (Garry Tan) — Claude Code skill-pack из 23+ slash-команд: что это, установка, требования,
  что создаёт, конфликты с нашей средой, профильность, вердикт. Изучено 2026-07-28 по README.
author: Дмитрий Заичкин, Claude (AI-assistant)
created: 2026-07-28
updated: 2026-07-28
review_cycle: 6m
last_reviewed: 2026-07-28
tags:
  - claude-code
  - skill-pack
  - оценка
---

# Справочник — Оценка gstack

## Краткая суть

- **gstack** (автор Гарри Тан, YC; MIT) — **Claude Code skill-pack**: 23+ специализированных slash-команд. Не MCP-сервер и не плагин, хотя включает CLI-утилиты.
- Модель: Think → Plan → Build → Review → Test → Ship → Reflect (роли CEO/eng-manager/designer/reviewer/QA/security/release).
- Ставится в `~/.claude/skills/gstack` (**user-уровень — глобально на всю машину**), правит `CLAUDE.md`, заводит `~/.gstack/`.
- **У нас НЕ установлен.** `bun` (обязателен) тоже НЕ стоит.
- **Вердикт: пока НЕ ставим.** Заточен под выпуск software-продуктов; для нашего маркетинга применимы 2-3 команды из 23 — не окупает глобальную установку и влияние на все проекты.

## Что это

Skill-pack «виртуальная инженерная команда» для solo-founder/малых команд: продуктовое планирование, дизайн-ревью, код-ревью, браузерный QA (реальный Chromium), security-аудит (OWASP+STRIDE), выпуск PR, деплой, пост-деплой-мониторинг, iOS-QA по USB, генерация PDF, персистентная память `/learn`.

## Как ставится (verbatim)

```
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack && cd ~/.claude/skills/gstack && ./setup
```

Командный режим (auto-update по репозиториям):

```
(cd ~/.claude/skills/gstack && ./setup --team) && ~/.claude/skills/gstack/bin/gstack-team-init required && git add .claude/ CLAUDE.md && git commit -m "require gstack for AI-assisted work"
```

## Требования

- Claude Code, Git.
- **Bun v1.0+** (у нас НЕ установлен).
- Node.js (на Windows), Chrome/Chromium (для браузерных навыков).

## Что создаёт / меняет

- `~/.claude/skills/gstack/` — установка; `~/.gstack/` — глобальное состояние (config, аналитика, browser cache); `.gstack/` — пер-проектное состояние; симлинки `.claude/skills/gstack-*`.
- **Правит `CLAUDE.md`** (добавляет список навыков gstack и browse-директивы).
- Локально: SQLite/`analytics.jsonl` (телеметрия, если включена); опционально GBrain (PGLite локально / Supabase облако).

## Конфликты с нашей средой

- **`~/.claude/skills` — глобальный уровень.** Навыки gstack включатся во **всех проектах машины, включая Breezeks**.
- **Правит `CLAUDE.md`** и добавляет slash-команды с обобщёнными глаголами (`/review`, `/ship`, `/qa`, `/learn`, `/browse`) → возможны пересечения имён с нашими навыками и подмена поведения в каждом проекте.
- У нас в `~/.claude/skills` — только свои навыки (Second Brain / KOS / Lark / research); прямого дубля gstack нет, но namespace-коллизии по generic-командам вероятны.

## Безопасность

- MIT, открытый код.
- **Телеметрия opt-in, по умолчанию ВЫКЛ**; шлёт только имя навыка/длительность/успех/версию/ОС — **не код, не пути, не промпты**. Отключение: `gstack-config set telemetry off`.
- ML-защита от prompt-injection (классификатор + опц. DeBERTa-ансамбль). GBrain — опционален, пер-репо trust-tiers, секрет-сканирование.

## Профильность для нас

Низкая: инструмент для **shipping ПО** (PR/CI/деплой/iOS/OWASP). Наша работа — маркетинг/контент/аналитика. Условно полезны `/make-pdf`, `/learn` (память), `/review` — ради 2-3 команд тащить весь пак и ставить Bun нецелесообразно.

## ruflo vs gstack (коротко)

- **ruflo** — тяжёлый мета-харнесс (демон + MCP + 98 агентов).
- **gstack** — легче: skill-pack из slash-команд, без демона и без постоянного MCP по умолчанию; ближе к тому, как мы уже работаем.
- Оба — под разработку ПО, оба ставятся так, что влияют на все проекты машины.

## Вердикт

**Пока не ставим.** Если пробовать — в изолированной папке проекта, не в user-global (иначе затрагивает Breezeks), и понимая, что это среда для выпуска ПО. Пересмотреть при появлении собственного девелоперского проекта.

# Связанные документы

- [Манифест контура](<_manifest.md>)
- [Справочник — Оценка ruflo](<Справочник — Оценка ruflo.md>)
