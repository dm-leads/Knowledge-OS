"""Сборка адаптеров источников по имени секции конфигурации.

Здесь — единственное место, где имя системы в конфигурации связывается с классом адаптера. Команды движка берут
системы по ролям конфигурации (система денежной модели, стороны моста), поэтому смена поставщика — это новый
адаптер здесь и правка конфигурации, а не правка движка.
"""
from __future__ import annotations

from .core.errors import GuardViolation
from .sources.amocrm_events import AmocrmEventsAdapter
from .sources.amocrm_mirror import AmocrmMirrorAdapter
from .sources.base import load_secrets
from .sources.direct import DirectAdapter
from .sources.metrika import MetrikaAdapter
from .sources.moysklad_probe import MoySkladProbe
from .sources.roistat import RoistatAdapter
from .sources.speech_analytics import SpeechAnalyticsAdapter
from .sources.webmaster import WebmasterAdapter
from .sources.yandex_search import YandexSearchAdapter

# Секция конфигурации → создание адаптера: (section, cfg, secrets, confirm_paid) -> адаптер.
FACTORIES = {
    "roistat": lambda section, cfg, secrets, confirm_paid: RoistatAdapter(section, cfg, secrets),
    "amocrm_mirror": lambda section, cfg, secrets, confirm_paid: AmocrmMirrorAdapter(section, cfg),
    "amocrm_events": lambda section, cfg, secrets, confirm_paid: AmocrmEventsAdapter(section, cfg, secrets),
    "metrika": lambda section, cfg, secrets, confirm_paid: MetrikaAdapter(section, cfg, secrets),
    "direct": lambda section, cfg, secrets, confirm_paid: DirectAdapter(section, cfg, secrets),
    "webmaster": lambda section, cfg, secrets, confirm_paid: WebmasterAdapter(section, cfg, secrets),
    "yandex_search": lambda section, cfg, secrets, confirm_paid:
        YandexSearchAdapter(section, cfg, secrets, confirm_paid=confirm_paid),
    "speech_analytics": lambda section, cfg, secrets, confirm_paid: SpeechAnalyticsAdapter(section, cfg, secrets),
    "moysklad": lambda section, cfg, secrets, confirm_paid: MoySkladProbe(section),
}


def build(name: str, raw: dict, cfg, secrets_path, confirm_paid: bool = False):
    """Адаптер системы по имени секции sources; секреты — только названные в секции."""
    if name not in raw["sources"]:
        raise GuardViolation(9, f"система «{name}» не объявлена в sources конфигурации")
    if name not in FACTORIES:
        raise GuardViolation(9, f"для системы «{name}» в движке нет адаптера")
    section = raw["sources"][name]
    return FACTORIES[name](section, cfg, load_secrets(secrets_path, section.get("secret_env", [])), confirm_paid)
