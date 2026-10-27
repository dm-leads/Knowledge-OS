"""Эмбеддинги судьи через облачный API (Т27.1, решение Дмитрия Д1: локальной модели нет): по желанию проекта.

Провайдер — адаптер инстанса по контракту ниже (`judge.embeddings.model: module:<путь>:<класс>`): у проектов разные
облака и ключи, пакет провайдера не выбирает. Пакет держит общее: кэш векторов `cache/embeddings/<sha>.json` (текст
не пересчитывается дважды), цену до вызова и бюджет-гейт (Т17.2), косинус. Без провайдера судья работает лексической
пятёркой слоя 2 — это путь по умолчанию.

Контракт провайдера: `Provider(instance)`; `price(texts) → рубли` (до вызова, без запроса); `embed(texts) → векторы`.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from ..core import budget


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(instance):
    from ..core.plugins import load_class
    return load_class(instance, instance.p["judge"]["embeddings"]["model"], {}, "judge.embeddings.model")(instance)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def vectors(instance, texts: list[str], *, tid: str, confirm, unattended: bool, provider=None) -> list[list[float]]:
    """Векторы текстов: из кэша, недостающие — одним платным вызовом через бюджет-гейт."""
    folder = instance.root / "cache" / "embeddings"
    folder.mkdir(parents=True, exist_ok=True)
    cached = {}
    for text in texts:
        path = folder / f"{_key(text)}.json"
        if path.is_file():
            cached[text] = json.loads(path.read_text(encoding="utf-8"))
    missing = [t for t in dict.fromkeys(texts) if t not in cached]
    if missing:
        provider = provider or load(instance)
        price = float(provider.price(missing))
        if price > 0:
            got = budget.paid_call(instance, tid, "судья: эмбеддинги", price, lambda: provider.embed(missing),
                                   confirm=confirm, unattended=unattended, what=f"эмбеддинги {len(missing)} текстов",
                                   source=f"judge.embeddings.price_per_1k_rub × знаков, {instance.today.isoformat()}")
        else:
            got = provider.embed(missing)
        for text, vector in zip(missing, got):
            (Path(folder) / f"{_key(text)}.json").write_text(json.dumps(vector), encoding="utf-8")
            cached[text] = vector
    return [cached[t] for t in texts]
