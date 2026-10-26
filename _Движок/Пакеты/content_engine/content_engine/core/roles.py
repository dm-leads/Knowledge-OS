"""Роли модельных шагов (Т17.1, Р11): пакет моделей не вызывает — он называет роль и модели в задании агенту.

У роли основная и запасная модель из профиля (`models`). Основная недоступна — задание выполняется запасной, конвейер
не встаёт. Выбор моделей — по навыку `llm-pipeline-economy`.

Модели меняются с обновлениями, и проект не привязывается к номеру версии навсегда (решение Дмитрия 26.10.2026):
имя с окончанием `-latest` (`claude-opus-latest`) значит «последняя версия семейства на день работы», её сверяют с
каталогом провайдера. `codex-latest` — вторая независимая модель (Codex CLI, навык `second-agent`): у неё нет памяти и
контекста проекта, поэтому она хороша там, где важна независимость, — вычитка фактов, второе мнение.
"""
from __future__ import annotations

from .errors import ProfileError
from .profile import ROLES

LATEST = "-latest"


def models(instance, role: str) -> tuple[str, str]:
    if role not in ROLES:
        raise ProfileError(f"роль «{role}» не из списка {ROLES}")
    entry = instance.p["models"][role]
    return entry["primary"], entry["fallback"]


def describe(name: str) -> str:
    """Имя модели для задания: псевдоним «последней версии» раскрывается словами."""
    if name == "codex" + LATEST:
        return "Codex CLI — вторая независимая модель, последняя версия (навык second-agent)"
    if name.endswith(LATEST):
        return f"{name[:-len(LATEST)]} — последняя версия семейства на день работы (сверить с каталогом провайдера)"
    return name
