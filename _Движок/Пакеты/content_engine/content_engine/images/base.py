"""Контракты картинок (Т30.1): провайдер делает файл, загрузчик кладёт его в хранилище сайта.

`ImageProvider(instance)`: `price(запрос) → рубли` до вызова, без запроса к сервису; `make(запрос, путь) → {file, url,
price}` — файл (или адрес готовой картинки) и фактическая цена, если сервис её вернул. Провайдер по виду картинки —
`images.providers` профиля: `none`, `template` (обложка и схема, 0 ₽), `library` (готовые фото проекта, 0 ₽) или
`module:<путь от инстанса>:<класс>` — платный генератор проекта; его вызов идёт через бюджет-гейт (Т17.2). Цену в
долларах или кредитах провайдер пересчитывает по `budget.usd_rub` и при курсе 0 отказывает до гейта.

`ImageUploader(instance)` — загрузка в хранилище сайта, запись вовне (`images.allow: write`): `target(файл, тема) →
адрес`, `snapshot(адрес) → {exists, content}`, `upload(файл, адрес)`, `read_back(адрес) → (ok, подробность)`,
`restore(снимок) → действие отката или строка-инструкция человеку`, если удалять загрузчик не умеет. Его реализует
провайдер (если умеет) или отдельный модуль `images.upload`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core import restore
from ..core.errors import EngineError

KINDS = ("cover", "illustration", "scheme", "product", "photo")


@dataclass
class Request:
    kind: str
    topic: str
    heading: str = ""
    text: str = ""
    alt: str = ""
    extra: dict = field(default_factory=dict)


def usd_to_rub(instance, usd: float) -> float:
    """Цена провайдера в долларах — в рубли по курсу профиля; курса нет — отказ до бюджет-гейта (код 3)."""
    rate = instance.p["budget"]["usd_rub"]
    if not rate:
        raise EngineError("budget.usd_rub = 0: цена провайдера в долларах, а курса нет — впишите курс ЦБ и его дату "
                          "(budget.usd_rub_source)")
    return round(usd * rate, 2)


def provider(instance, kind: str):
    """Провайдер вида картинки из профиля; `none` — None."""
    from ..core.plugins import load_class
    from .library import LibraryProvider
    from .template import TemplateProvider
    spec = instance.p["images"]["providers"].get(kind, "none")
    if spec == "none":
        return None
    return load_class(instance, spec, {"template": TemplateProvider, "library": LibraryProvider},
                      f"images.providers.{kind}")(instance)


def uploader(instance):
    """Загрузчик: модуль `images.upload` или платный провайдер, который умеет загружать; иначе None (загружает человек)."""
    from ..core.plugins import load_class
    spec = instance.p["images"]["upload"]
    if spec != "none":
        return load_class(instance, spec, {}, "images.upload")(instance)
    for kind_spec in instance.p["images"]["providers"].values():
        if kind_spec.startswith("module:"):
            candidate = load_class(instance, kind_spec, {}, "images.providers")(instance)
            if all(hasattr(candidate, m) for m in ("target", "snapshot", "upload", "read_back", "restore")):
                return candidate
    return None


@restore.register("image_upload")
def _restore_upload(instance, snap: dict):
    up = uploader(instance)
    if up is None:
        raise EngineError("загрузчика картинок нет в профиле — откат загрузки вручную")
    return up.restore(snap)


@restore.register("image_backfill")
def _restore_backfill(instance, snap: dict):
    """Добор картинок, поставленный в очередь прогоном `images backfill`, — снимается удалением его записи."""
    from pathlib import Path
    from ..core.actions import Action
    path = Path(snap["meta"]["queued"])

    def do():
        if path.is_file():
            path.unlink()
        return path

    return Action("image_backfill_rollback", str(path), "delete", f"снять добор картинок {snap['target']}",
                  "read_back", ("images", "files"), do=do, verify=lambda _: (not path.exists(), "добор снят"),
                  snapshot=lambda: {"exists": path.exists(), "read_from": str(path), "content": None})
