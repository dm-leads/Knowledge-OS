"""Нормализация запросов и смысловой ключ (Т5).

Порядок: нижний регистр, ё → е, только буквы и цифры, стоп-слова (общий список пакета плюс профиль), слова короче
трёх букв отбрасываются, основа — стеммером Snowball. Беглая гласная: у основы от четырёх букв на «согласная + е/о +
согласная (+ь)» есть второй вариант без этой гласной («котел» → «котл», «окон» → «окн»). Гласная не снимается, а
добавляется вариант: «свет» остаётся «свет», лишний «свт» ни с чем не совпадает. Трёхбуквенные основы варианта не
получают — иначе «мел» и «мол» склеиваются через «мл». Слово совпадает с другим, если совпал любой вариант основы.
"""
from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

import snowballstemmer

WORD = re.compile(r"[a-zа-я0-9]+")
CONSONANTS = set("бвгджзйклмнпрстфхцчшщ")
MIN_WORD = 3

_STEMMER = snowballstemmer.stemmer("russian")


def _package_stop_words() -> frozenset[str]:
    text = resources.files("content_engine.data").joinpath("stopwords_ru.txt").read_text(encoding="utf-8")
    return frozenset(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))


PACKAGE_STOP_WORDS = _package_stop_words()


def words(text: str) -> list[str]:
    """Слова текста: нижний регистр, ё → е, только буквы и цифры."""
    return WORD.findall((text or "").lower().replace("ё", "е"))


@lru_cache(maxsize=200_000)
def stem_variants(word: str) -> frozenset[str]:
    """Варианты основы одного слова (уже нормализованного)."""
    stem = _STEMMER.stemWord(word)
    variants = {stem}
    core = stem[:-1] if stem.endswith("ь") else stem
    if (len(core) >= 4 and core[-1] in CONSONANTS and core[-2] in "ео" and core[-3] in CONSONANTS):
        variants.add(core[:-2] + core[-1])
    return frozenset(variants)


class Normalizer:
    """Нормализатор проекта: общий список стоп-слов пакета плюс `stop_words` профиля."""

    def __init__(self, extra_stop_words=()):
        self.stop = PACKAGE_STOP_WORDS | frozenset(w for item in extra_stop_words for w in words(item))

    def tokens(self, text: str) -> list[frozenset[str]]:
        """Слова текста по порядку — каждое множеством вариантов основы; стоп-слова и короткие выброшены."""
        return [stem_variants(w) for w in words(text) if len(w) >= MIN_WORD and w not in self.stop]

    def key(self, text: str) -> "Key":
        """Смысловой ключ — множество основ; словоформы одного слова дают одну основу."""
        parts: list[frozenset[str]] = []
        for token in self.tokens(text):
            if not any(token & part for part in parts):
                parts.append(token)
        return Key(tuple(parts))


class Key:
    """Смысловой ключ: основы по одной на слово, каждая — множеством вариантов."""

    __slots__ = ("parts", "variants")

    def __init__(self, parts: tuple[frozenset[str], ...]):
        self.parts = parts
        self.variants = frozenset(v for part in parts for v in part)

    def __len__(self):
        return len(self.parts)

    def __bool__(self):
        return bool(self.parts)

    def similarity(self, other: "Key") -> float:
        """Доля основ этого ключа, найденных в другом: |K ∩ R| / |K| (Т5.2)."""
        if not self.parts:
            return 0.0
        return sum(1 for part in self.parts if part & other.variants) / len(self.parts)

    def stems(self) -> list[str]:
        """Основы для печати: по одной на слово — сама основа Snowball (вариант без беглой гласной короче)."""
        return [max(part, key=lambda v: (len(v), v)) for part in self.parts]

    def text(self) -> str:
        return " ".join(sorted(self.stems()))

    def canonical(self) -> str:
        """Вид для склейки: у каждой основы — самый короткий вариант («котел» и «котл» дают одно)."""
        return " ".join(sorted(min(part, key=lambda v: (len(v), v)) for part in self.parts))

    def to_list(self) -> list[list[str]]:
        return [sorted(part) for part in self.parts]

    @classmethod
    def from_list(cls, data) -> "Key":
        return cls(tuple(frozenset(part) for part in data))

    def __repr__(self):
        return f"Key({self.stems()})"
