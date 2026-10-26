"""Адаптеры проекта из инстанса (Р12): строка профиля `module:<путь от инстанса>:<класс>`.

Сборщик, публикатор или запись в базу конкретного стека живут в репозитории проекта: там их данные и вёрстка. Ядро
загружает класс по пути файла и зовёт его по тому же контракту, что и встроенные реализации.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .errors import EngineError, ProfileError


def load_class(instance, spec: str, builtin: dict, field: str):
    """Класс по строке профиля: имя встроенной реализации или `module:<путь>:<класс>`."""
    if spec in builtin:
        return builtin[spec]
    if not spec.startswith("module:"):
        raise ProfileError(f"поле «{field}»: «{spec}» — не встроенная реализация {tuple(builtin)} и не module:…")
    _, rel, name = spec.split(":", 2)
    path = Path(rel)
    path = path if path.is_absolute() else instance.root / path
    if not path.is_file():
        raise EngineError(f"поле «{field}»: файл адаптера не найден — {path}")
    module_name = f"content_instance_{path.stem}_{abs(hash(str(path)))}"
    loader_spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(loader_spec)
    try:
        loader_spec.loader.exec_module(module)
    except Exception as exc:
        raise EngineError(f"поле «{field}»: адаптер {path.name} не загрузился — {type(exc).__name__}: {exc}") from exc
    cls = getattr(module, name, None)
    if cls is None:
        raise EngineError(f"поле «{field}»: в {path.name} нет класса {name}")
    return cls
