---
area: ekosistema-instrumentov
type: reference
status: active
description: |
  Оценка anthropics/claude-cookbooks — официальный учебный репозиторий Anthropic (Jupyter-ноутбуки по
  Claude API). Не плагин/скилл/MCP, ничего не ставит. Что забрать под наши задачи. Изучено 2026-07-28.
author: Дмитрий Заичкин, Claude (AI-assistant)
created: 2026-07-28
updated: 2026-07-28
review_cycle: 6m
last_reviewed: 2026-07-28
tags:
  - claude-code
  - reference-library
  - anthropic
  - оценка
---

# Справочник — Оценка claude-cookbooks

## Краткая суть

- **anthropics/claude-cookbooks** — официальный учебный репозиторий Anthropic (50k★, MIT): Jupyter-ноутбуки и примеры кода по Claude API.
- **Это НЕ плагин, НЕ скилл, НЕ MCP.** Ничего не устанавливает в Claude Code и в систему — просто клонируешь и читаешь/адаптируешь код.
- **Конфликтов с нашей средой нет в принципе** — переопределять нечего, `~/.claude/skills` не трогает.
- **Вердикт: забрать — да, но как read-only библиотеку паттернов** (cherry-pick рецептов), не как инструмент для установки. Ноль риска для среды.

## Что это

Reference/learning-репозиторий: ~50+ ноутбуков по паттернам работы с Claude API. Python + Jupyter, менеджер пакетов `uv`, Make/Tox. Внутри есть свои `.claude/` и `CLAUDE.md`, но они — гайд для контрибьюторов самого репо, не для установки куда-либо.

## Структура (топ-уровень)

`capabilities/` (classification, RAG, summarization), `tool_use/`, `multimodal/` (vision, charts, PDF, transcribe), `third_party/` (Pinecone, Wikipedia, VoyageAI), `extended_thinking/`, `finetuning/`, `coding/`, `claude_agent_sdk/`, `managed_agents/`, `patterns/agents/`, `skills/` (custom_skills, skill_utils.py), `evals/`, `observability/`, `misc/` (prompt caching, JSON mode, PDF, moderation).

## Что забрать под наши задачи (cherry-pick)

| Ноутбук / раздел | Зачем нам |
|---|---|
| `misc/prompt_caching.ipynb` | Массовые LLM-прогоны (речевая аналитика) → экономия |
| `evals/` + `misc/building_evals.ipynb` | Формализовать гейты качества / критиков |
| `capabilities/` (classification, RAG, summarization) | Речевая аналитика: продажа/сервис, RAG по транскриптам, выжимки |
| `skills/` (`custom_skills`, `skill_utils.py`) | Эталонные примеры как строить наши навыки |
| `patterns/agents/` + `claude_agent_sdk/` | Паттерны оркестрации субагентов/воркфлоу |
| `multimodal/` (charts, PDF, transcribe) | Разбор xlsx Вебмастера, PDF, alt к картинкам |
| `third_party/VoyageAI`, `Pinecone` | Эмбеддинги/векторный поиск |
| `misc/building_moderation_filter.ipynb` | Модерация UGC/отзывов |

## Требования и стоимость

- **Читать** ноутбуки — бесплатно.
- **Запускать** — Python + `uv` (`make install` / `uv sync`) + `ANTHROPIC_API_KEY`; каждый прогон жжёт кредиты (vision/длинный контекст/extended thinking — дороже).

## Конфликты с нашей средой

- Нет. Не устанавливается в Claude Code, не меняет глобальную среду.
- Единственная гигиена: не клонировать ВНУТРЬ рабочего волта (его `CLAUDE.md` может путаться) — держать отдельной standalone-папкой.

## Где лежит и как используется у нас (2026-07-28)

- **Локальный клон** (поверхностный, read-only): `C:\Users\redmi\Reference\claude-cookbooks` — нейтральная Reference-папка, не внутри Breezeks-Git и не «внутри» KOS. Обновление: `git pull` в этой папке.
- **Авто-доступ во всех чатах на устройстве:** глобальный навык `~/.claude/skills/anthropic-cookbook` (SKILL.md с индексом «потребность → ноутбук»). Claude сам подхватывает его по смыслу запроса («хочу рецепт Claude API», «учебный репо Антропика», кэш/evals/RAG/классификация/vision/навыки и т.п.) в любом проекте.
- **Правило:** ноутбуки читаем бесплатно; запуск (Python + `uv` + `ANTHROPIC_API_KEY`, тратит кредиты) — только по явной просьбе.
- Границы: навык лежит в конфиге харнесса `~/.claude`, а не в репозиториях; в Breezeks-Git ничего про KOS/локальные файлы не попадает.

## Отличие от ruflo / gstack

- ruflo/gstack — **исполняемые харнессы**, ставятся в среду и влияют на все проекты (вердикт: пока не ставим).
- claude-cookbooks — **пассивный справочник кода**, ничего не ставит (вердикт: забрать как read-only библиотеку).

## Вердикт

**Забрать — да, как read-only библиотеку паттернов.** Клонировать в отдельную standalone-папку, вытаскивать конкретные рецепты по мере надобности. Для прода не крутим — ценность как справочник.

# Связанные документы

- [Манифест контура](<_manifest.md>)
- [Справочник — Оценка ruflo](<Справочник — Оценка ruflo.md>)
- [Справочник — Оценка gstack](<Справочник — Оценка gstack.md>)
