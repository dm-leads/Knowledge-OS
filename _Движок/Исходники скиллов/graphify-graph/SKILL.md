---
name: graphify-graph
description: >
  Use when the user wants to understand a code repository through a graph — "build a graph of this
  repo/codebase", "graphify this", "find the root cause", "how is X connected / called", "what depends
  on X / what breaks if I change X", "show architectural hubs / god nodes", "explain this function's
  neighbors", "shortest path between A and B in the code". Wraps the local graphify CLI in SAFE code-mode
  (local AST, no API key, nothing leaves the machine). Applies per-repo; never installs graphify globally.
  For root-cause work that also needs events/analytics/tickets, note that semantic mode sends data to a
  backend — only with a LOCAL backend (Ollama) and never on PII/secrets.
---

# graphify-graph — граф по коду для навигации и корневых причин

Обёртка над локальным CLI `graphify` (уже установлен: `uv tool install graphifyy`). Строит граф символов
кода (функции, методы, вызовы, импорты) и даёт агенту навигировать по нему: хабы, зависимости, пути,
корневые причины. **Визуализация нужна агенту, не человеку** — по графу быстро добегаешь до сути.

## Дисциплина (не нарушать)

- **Только код-режим:** `graphify extract <path> --code-only` — локальный AST, **без API-ключа**, ничего не уходит наружу.
- **Вывод — в скретч, не в репозиторий:** `--out <scratch-dir>` (пишет `<scratch-dir>/graphify-out/`). Не засоряем рабочий репо и git.
- **НЕ запускать** `graphify install`, `graphify claude install`, `hook install` — они правят `CLAUDE.md` и ставят хуки. Нам нужен только CLI.
- **Данные наружу:** семантический режим (`extract` без `--code-only`, доки/PDF) шлёт содержимое в бэкенд. На ПДн/секреты — **нельзя**; если очень нужно по докам — только локальный бэкенд Ollama и обезличенный срез.
- Применяем **по одному репозиторию** (проектно), не глобально.

## Рецепт

1. **Построить граф (локально):**
   ```
   graphify extract "<repo-or-src-path>" --code-only --out "<scratch-dir>"
   ```
   → `<scratch-dir>/graphify-out/graph.json` (узлы/рёбра/сообщества).

2. **Запросить граф (детерминированно, без LLM):** во всех командах указывай `--graph "<scratch-dir>/graphify-out/graph.json"`.
   - `graphify god-nodes --graph <g> --top 12` — архитектурные хабы (вокруг чего крутится код).
   - `graphify query "<вопрос>" --graph <g> --budget 1500` — BFS-обход графа под вопрос (узлы + файл:строка).
   - `graphify explain "<symbol>" --graph <g>` — узел и его соседи простым языком.
   - `graphify path "A" "B" --graph <g>` — кратчайший путь между двумя символами.
   - `graphify affected "<symbol>" --graph <g> --depth 2` — что сломается при изменении X (обратный обход).

3. **Прочитать `GRAPH_REPORT.md`** (если сгенерирован) и/или открыть `graph.html` — интерактивная визуализация.
   Именование сообществ (`cluster-only`/`label`) требует LLM-бэкенда — по умолчанию **пропускаем** (стоит денег); граф и запросы работают и без имён.

4. **Ответить пользователю:** свести находки (хабы, зависимости, где корень проблемы) со ссылками `файл:строка`.

## Когда это ценно

- Понять/онбордить незнакомый или наш код (MCP-серверы, скрипты, пайплайны): за минуту видно центр и связи.
- Корневые причины: связать симптом → функции → вызовы. Для полного root-cause по продукту нужны ещё события/тикеты — это уже семантический режим с локальным бэкендом.
- Предел: для одного лендинга/текста — избыточно. Ценность появляется на реальном коде/данных.

## Пример (обкатано на нашем Lark MCP)

```
graphify extract "…/MCP — Lark/server/src" --code-only --out "<scratch>/graphify_lark"
graphify god-nodes --graph "<scratch>/graphify_lark/graphify-out/graph.json" --top 12
```
Результат: 423 узла / 879 рёбер / 13 сообществ; хабы — `requestLark()` (центральный вызов API, 32 связи),
`normalizeApiData()`, `resolveP2PChatId()`; `query "token refresh and OAuth"` вывел `tokenModeFromUseUAT()`,
`buildOAuthScopes()`, `refreshGroupCache()` с адресами `файл:строка`.

## Оценка инструмента и правила подключения

Разбор и вердикт — `D:\Knowledge-OS\Экосистема инструментов\03 — Ресурсы\Оценки инструментов\Справочник — Оценка graphify.md`
(и матрица подключения там же). Дистилляция эфира Красинского про graphify — в `D:\Knowledge-OS\Методологии\Эфиры (разборы)\`.
