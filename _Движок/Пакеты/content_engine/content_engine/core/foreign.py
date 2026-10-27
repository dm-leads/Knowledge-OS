"""Чужие целевые ключи (Т19.2, Т19.4, гейт статьи `foreign_keys` и `foreign_mentions`): одно правило на все шаги.

- «взят» — заголовок почти совпадает с целевым ключом чужой страницы в обе стороны (`title_similarity ≥ порога`):
  короткий общий ключ внутри длинного подзаголовка о другом предмете — не захват;
- «упомянут без ссылки» — только если не «взят»: ключ не короче трёх основ целиком входит во фразу, фраза не длиннее
  трёх длин ключа, ссылки на эту страницу нет.
Гейт статьи проверяет так текст, бриф — свои заголовки: страница, чей ключ задевает подзаголовок брифа, попадает в
кандидаты перелинковки «по подзаголовку», и гейт статьи потом не требует ссылки, которой нет в кандидатах (Н2).
"""
from __future__ import annotations

from dataclasses import dataclass

from .urls import slug_of


@dataclass(frozen=True)
class Hit:
    page: dict          # страница карты спроса
    target: str         # её целевой ключ
    phrase: str         # заголовок или фраза, которые его задели


def foreign_hits(pages: list[dict], heads: list[tuple], sentences: list[tuple], links: set[str], norm,
                 threshold: float, own_slug: str = "") -> tuple[list[Hit], list[Hit]]:
    """(взятые, упомянутые без ссылки). heads и sentences — пары (текст, ключ нормализации)."""
    taken, mentioned = [], []
    for page in pages:
        if page["id"] == own_slug or not page["target_keys"]:
            continue
        linked = any(page["url"] in link or slug_of(link) == page["id"] for link in links)
        for target in page["target_keys"]:
            tk = norm.key(target)
            if len(tk) < 2:
                continue
            hit = next((h for h, hk in heads if title_similarity(tk, hk) >= threshold), None)
            if hit:
                taken.append(Hit(page, target, hit))
            elif len(tk) >= 3 and not linked:
                phrase = next((s for s, sk in sentences if sk and tk.similarity(sk) == 1.0 and len(sk) <= 3 * len(tk)),
                              None)
                if phrase:
                    mentioned.append(Hit(page, target, phrase))
    return taken, mentioned


def title_similarity(a, b) -> float:
    from ..steps.dedup import title_similarity as similarity
    return similarity(a, b)
