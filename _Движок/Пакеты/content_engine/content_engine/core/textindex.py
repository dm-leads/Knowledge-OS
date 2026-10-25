"""Индекс текстов корпуса для слоя 2 анти-дублей: сколько раз основа встречается в статье и в каждом её разделе.

Считается по словам после нормализации (Т5): основа совпадает, если совпал любой её вариант. Строится в памяти за
один проход по корпусу; ключи страниц (заголовок, заголовок с подзаголовками) — тут же.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .normalize import Key, Normalizer


@dataclass
class IndexedSection:
    heading: str
    counts: Counter


@dataclass
class IndexedPage:
    page: object                  # adapters.corpus.Page
    counts: Counter               # множество вариантов слова → сколько раз
    sections: list[IndexedSection]
    title_key: Key
    key: Key                      # заголовок и подзаголовки (Т8.2а)


class TextIndex:
    def __init__(self, pages, normalizer: Normalizer):
        self.normalizer = normalizer
        self.by_variant: dict[str, set[frozenset]] = {}
        self.pages: list[IndexedPage] = []
        for page in pages:
            sections, total = [], Counter()
            for section in page.sections:
                counts = Counter(normalizer.tokens(f"{section.heading}\n{section.text}"))
                total.update(counts)
                sections.append(IndexedSection(section.heading, counts))
            for token in total:
                for variant in token:
                    self.by_variant.setdefault(variant, set()).add(token)
            self.pages.append(IndexedPage(page, total, sections, normalizer.key(page.title),
                                          normalizer.key(" ".join([page.title, *page.headings]))))
        self._matches: dict[frozenset, set[frozenset]] = {}

    def matches(self, part: frozenset) -> set[frozenset]:
        """Все формы слов корпуса, у которых совпадает хоть один вариант основы."""
        found = self._matches.get(part)
        if found is None:
            found = set().union(*(self.by_variant.get(v, set()) for v in part))
            self._matches[part] = found
        return found

    def count(self, counts: Counter, part: frozenset) -> int:
        return sum(counts.get(token, 0) for token in self.matches(part))
