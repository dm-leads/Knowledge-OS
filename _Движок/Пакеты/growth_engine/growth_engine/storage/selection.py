"""Выбор хранилища команды: папка markdown (`--out`), книга Lark (`--lark-book`) или книга Google (`--google-book`),
у переноса — ещё сухой прогон.

Книга задаётся только явным ключом после «да» владельца книги (Х6) или адаптером в конфигурации инстанса. Мост
поднимается один раз на команду и закрывается при выходе. Google добавлен 24.09.2026, когда у Lark кончилась месячная
квота вызовов: логика таблицы у обоих общая (`TableBackend`), различается только мост.

С 28.09.2026 выбор хранилища живёт только здесь: общие команды не знают имён мостов, получают фабрики мостов
параметрами (для тестов) и спрашивают про ключи хранилища `given_store_keys`.
"""
from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from ..core.errors import GuardViolation
from ..core.storage import RegistryStore
from .google_sheets import GoogleBridge
from .labels import LabelledBackend
from .lark_sheets import LarkBridge
from .markdown import MarkdownBackend
from .table import TableBackend

# Ключи хранилища: имя в разобранных аргументах и ключ командной строки — в порядке, в котором они печатаются.
STORE_KEYS = (("out", "--out"), ("lark_book", "--lark-book"), ("google_book", "--google-book"))
STORE_KEYS_TEXT = "--out (папка markdown), --lark-book (книга Lark) или --google-book (книга Google)"


def given_store_keys(args) -> list[str]:
    """Ключи хранилища, заданные в команде: пустой список — не задано ни одного."""
    return [flag for name, flag in STORE_KEYS if getattr(args, name, None)]


def add_store_arguments(parser, dry_run: bool = False, required: bool = True) -> None:
    """Ключи хранилища: не больше одного из --out, --lark-book и --google-book (и --dry-run, если команда его
    допускает); если `required` — ровно один."""
    group = parser.add_mutually_exclusive_group(required=required)
    if dry_run:
        group.add_argument("--dry-run", action="store_true", help="напечатать план записи и ничего не писать")
    group.add_argument("--out", help="папка хранилища markdown")
    group.add_argument("--lark-book", help="токен книги Lark — только после «да» владельца книги")
    group.add_argument("--google-book", help="ключ книги Google Sheets — только после «да» владельца книги")


def store_target(args, raw: dict):
    """Хранилище команды из конфигурации инстанса: папка markdown из --out или книга из `storage`; иначе — стоп
    (страж 9). Явный ключ всегда сильнее конфигурации."""
    if getattr(args, "out", None):
        return Namespace(out=args.out, lark_book=None, google_book=None)
    if getattr(args, "lark_book", None):
        return Namespace(out=None, lark_book=args.lark_book, google_book=None)
    if getattr(args, "google_book", None):
        return Namespace(out=None, lark_book=None, google_book=args.google_book)
    storage = raw.get("storage") or {}
    if storage.get("adapter") == "lark_sheets" and storage.get("book"):
        return Namespace(out=None, lark_book=storage["book"], google_book=None)
    if storage.get("adapter") == "google_sheets" and storage.get("book"):
        return Namespace(out=None, lark_book=None, google_book=storage["book"])
    raise GuardViolation(9, "хранилище не задано: в конфигурации нет адаптера lark_sheets или google_sheets с книгой — "
                            "укажите --out")


@contextmanager
def open_store(args, domains, today=date.today, bridge_factory=None, google_factory=None):
    """Хранилище по ключам команды: (RegistryStore, подпись для отчёта). Фабрика моста не задана — настоящий мост."""
    out, book = getattr(args, "out", None), getattr(args, "lark_book", None)
    google = getattr(args, "google_book", None)
    if len(given_store_keys(args)) != 1:
        raise GuardViolation(13, f"хранилище: нужно ровно одно — {STORE_KEYS_TEXT}")
    if out:
        yield RegistryStore(MarkdownBackend(Path(out)), allowed_domains=domains, today=today), f"markdown «{out}»"
        return
    if google:
        bridge = (google_factory or GoogleBridge)()
        try:
            # Книга Google — рабочая книга человека: русские заголовки (решение владельца 24.09.2026).
            yield (RegistryStore(LabelledBackend(TableBackend(google, bridge)), allowed_domains=domains,
                                 today=today),
                   f"Google, книга {google}")
        finally:
            bridge.close()
        return
    bridge = (bridge_factory or LarkBridge)()
    try:
        yield RegistryStore(TableBackend(book, bridge), allowed_domains=domains, today=today), f"Lark, книга {book}"
    finally:
        bridge.close()
