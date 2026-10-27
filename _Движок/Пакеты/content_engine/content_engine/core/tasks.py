"""Задание агенту файлом (Т17.1, Р11): `data/briefs/<id>/task_<имя>.md`.

Шапка задания — роль, основная и запасная модель, правило «основная недоступна — выполнять запасной», путь файла
результата и его схема, просьба сообщить токены в поле `tokens` результата (необязательно: без них расход — «нет
данных», а не ноль). Результат проверяет код, а не агент.
"""
from __future__ import annotations

from pathlib import Path

from . import roles


def write_task(instance, topic_id: str, role: str, name: str, body: str, *, result: str, schema: str,
               folder: Path | None = None) -> Path:
    """`folder` — своя папка задания: вычитке фактов не дают рабочую папку темы с брифом и заданиями автора (Т29.1)."""
    primary, fallback = roles.models(instance, role)
    folder = folder or instance.brief_dir(topic_id)
    head = [
        f"# Задание: {name}, тема {topic_id}",
        "",
        f"- Роль: {role}",
        f"- Модель: основная {roles.describe(primary)}; запасная {roles.describe(fallback)}. Основная недоступна — "
        "выполнить запасной; конвейер не встаёт.",
        f"- Результат — файл `{folder / result}` по схеме ниже. Его проверит код "
        f"(`content-engine prepare check {topic_id}` или проверка шага), а утвердит человек.",
        "- Сообщите расход токенов полем `tokens` в результате (число). Не знаете — не пишите: расход будет «нет "
        "данных», а не ноль. Полем `model_used` — какая модель и версия выполнили задание на деле.",
        f"- Составлено: {instance.today.isoformat()}",
        "",
        "## Схема результата",
        "",
        "```yaml",
        schema.rstrip(),
        "```",
        "",
    ]
    path = folder / f"task_{name}.md"
    path.write_text("\n".join(head) + body.rstrip() + "\n", encoding="utf-8", newline="\n")
    return path
