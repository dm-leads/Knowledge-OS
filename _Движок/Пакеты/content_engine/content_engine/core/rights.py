"""Права на запись ступенями (Т16.1, Р14): запрет в коде, а не в промпте.

Ступени: `publish.allow` — files < commit < publish; `indexing.allow` и `records.allow` — files < write. По умолчанию
`files`: машина пишет только файлы своего инстанса. Ступень выше — строка профиля по разрешению владельца проекта с
записью в журнале действий проекта. Действие выше ступени не попадает в план; прямой вызов — `RightsRefused` (код 1).
"""
from __future__ import annotations

from .errors import ProfileError, RightsRefused
from .profile import ALLOW


def level(instance, area: str) -> str:
    if area not in ALLOW:
        raise ProfileError(f"неизвестная область прав «{area}»; области — {tuple(ALLOW)}")
    return instance.p[area]["allow"]


def allows(instance, area: str, need: str) -> bool:
    steps = ALLOW[area]
    if need not in steps:
        raise ProfileError(f"ступень «{need}» не из списка {area}: {steps}")
    return steps.index(level(instance, area)) >= steps.index(need)


def refusal(area: str, need: str) -> str:
    return f"нужно разрешение: {area}.allow = {need} (строка профиля по разрешению владельца, запись в журнале действий)"


def require(instance, area: str, need: str):
    if not allows(instance, area, need):
        raise RightsRefused(refusal(area, need) + f"; сейчас — {level(instance, area)}")


def split(instance, actions) -> tuple[list, list[str]]:
    """Действия, которые ступень пропускает, и тексты отказов по остальным (Т21.4)."""
    allowed, refused = [], []
    for action in actions:
        area, need = action.right
        if allows(instance, area, need):
            allowed.append(action)
        else:
            refused.append(f"{action.kind} → {action.target}: {refusal(area, need)}")
    return allowed, refused
