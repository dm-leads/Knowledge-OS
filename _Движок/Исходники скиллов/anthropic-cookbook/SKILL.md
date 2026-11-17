---
name: anthropic-cookbook
description: >-
  Рецепты Claude API из локальной копии anthropics/claude-cookbooks для любого проекта: кэш промптов, пакетная
  обработка, оценки, RAG, классификация, извлечение JSON, инструменты, агенты и мультиагенты, Agent Skills,
  расширенное мышление. Фразы: «учебный репо Антропика», «cookbook», «какие рецепты помогут», «какие навыки
  помогут», «как это делает Anthropic». Ноутбуки читать бесплатно; запускать — только по явной просьбе (тратит
  кредиты API).
---

# Anthropic Cookbook — local reference library

Официальный учебный репозиторий Anthropic (`anthropics/claude-cookbooks`, MIT) склонирован локально как
**пассивный справочник паттернов**. Это НЕ исполняемая среда — читаем код и переносим паттерн в задачу.

**Локальный путь:** `C:\Users\redmi\Reference\claude-cookbooks`

## Когда вызывать

Ещё темы: генерация тестовых случаев, contextual embeddings, граф знаний, суммаризация, text-to-SQL, vision (графики, PDF, расшифровка), память и сжатие контекста, цитаты, модерация.

Запуск ноутбука требует Python, uv и ANTHROPIC_API_KEY и тратит кредиты — только если владелец прямо попросил.

## Как использовать

1. Определи потребность пользователя → выбери ноутбук по индексу ниже.
2. **Прочитай** нужный `.ipynb`/`.py`/`guide.ipynb` из локального пути (Read).
3. Перенеси паттерн в задачу пользователя (адаптируй под его данные/провайдера/канон проекта).
4. **Не запускай** ноутбуки по умолчанию — запуск требует Python + `uv` + `ANTHROPIC_API_KEY` и тратит
   кредиты. Запускать только по явной просьбе; перед платным прогоном — озвучить стоимость.
5. Если точного рецепта нет — скажи об этом, не выдумывай путь. Полный список: `registry.yaml` в корне репо.

## Индекс: потребность → ноутбук (пути от корня репо)

| Потребность | Файл |
|---|---|
| Кэш промптов (экономия на массовых прогонах) | `misc/prompt_caching.ipynb`, `misc/speculative_prompt_caching.ipynb` |
| Пакетная обработка (batch API) | `misc/batch_processing.ipynb` |
| Построение evals / метрик качества | `misc/building_evals.ipynb`, `misc/generate_test_cases.ipynb` |
| Классификация (напр. продажа/сервис) | `capabilities/classification/guide.ipynb` |
| RAG по документам/транскриптам | `capabilities/retrieval_augmented_generation/guide.ipynb`, `capabilities/contextual-embeddings/guide.ipynb` |
| Граф знаний | `capabilities/knowledge_graph/guide.ipynb` |
| Суммаризация | `capabilities/summarization/guide.ipynb` |
| Текст → SQL / запросы к БД | `capabilities/text_to_sql/guide.ipynb`, `misc/how_to_make_sql_queries.ipynb` |
| Структурированный JSON-вывод | `misc/how_to_enable_json_mode.ipynb`, `tool_use/extracting_structured_json.ipynb`, `tool_use/tool_use_with_pydantic.ipynb` |
| Tool use (базово / выбор инструмента / параллельно) | `tool_use/calculator_tool.ipynb`, `tool_use/tool_choice.ipynb`, `tool_use/parallel_tools.ipynb` |
| Агент обслуживания клиентов | `tool_use/customer_service_agent.ipynb` |
| Память агента / компакция контекста | `tool_use/memory_cookbook.ipynb`, `misc/session_memory_compaction.ipynb`, `tool_use/automatic-context-compaction.ipynb` |
| Паттерны агентов и оркестрации | `patterns/agents/basic_workflows.ipynb`, `patterns/agents/orchestrator_workers.ipynb`, `patterns/agents/evaluator_optimizer.ipynb`, `patterns/agents/async_multi_agent_orchestration.ipynb` |
| Построение Agent Skills | `skills/notebooks/01_skills_introduction.ipynb`, `skills/notebooks/03_skills_custom_development.ipynb` |
| Claude Agent SDK (готовые агенты) | `claude_agent_sdk/00_The_one_liner_research_agent.ipynb` … `07_Hosting_the_agent.ipynb` |
| Vision: графики/PPT/PDF | `multimodal/reading_charts_graphs_powerpoints.ipynb`, `multimodal/best_practices_for_vision.ipynb` |
| Vision: транскрибация текста с изображения | `multimodal/how_to_transcribe_text.ipynb` |
| Haiku как субагент | `multimodal/using_sub_agents.ipynb` |
| PDF: загрузка и суммаризация | `misc/pdf_upload_summarization.ipynb` |
| Модерация контента | `misc/building_moderation_filter.ipynb` |
| Цитаты (attribution к источнику) | `misc/using_citations.ipynb` |
| Extended thinking | `extended_thinking/extended_thinking.ipynb`, `extended_thinking/extended_thinking_with_tool_use.ipynb` |
| Эмбеддинги (Voyage) / RAG на внешних БД | `third_party/VoyageAI/how_to_create_embeddings.md`, `third_party/Pinecone/rag_using_pinecone.ipynb`, `third_party/MongoDB/rag_using_mongodb.ipynb` |
| Аудио STT (сторонние) | `third_party/Deepgram/prerecorded_audio.ipynb`, `third_party/ElevenLabs/low_latency_stt_claude_tts.ipynb` |
| Чтение веб-страниц дёшево (Haiku) | `misc/read_web_pages_with_haiku.ipynb` |
| Фронтенд-эстетика (промптинг) | `coding/prompting_for_frontend_aesthetics.ipynb` |

## Обновление

Библиотека — поверхностный клон. Обновить: `cd C:\Users\redmi\Reference\claude-cookbooks && git pull`.
Оценка инструмента и вердикт — в `D:\Knowledge-OS\Экосистема инструментов\03 — Ресурсы\Оценки инструментов\Справочник — Оценка claude-cookbooks.md`.
