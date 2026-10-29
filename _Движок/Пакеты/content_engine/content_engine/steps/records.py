"""Записи в день выкладки (Г4, Т23): `content-engine publish record <id>`; сразу после «доехало» их делает verify.

Каждая запись сначала проверяет, есть ли она уже (Т23.4) — повторный запуск ничего не дублирует:
- бронь → `converted` с адресом; бронь, истёкшая, пока писалась статья, тоже конвертируется — с пометкой;
- тема → `published`;
- карта спроса — страница со своим кластером, целевыми ключами из запросов кластера, видом «статья», источником «запись
  выпуска»; кластер — `covered`. Пересборка карты (`map build`) дочитывает такие страницы из журнала выпуска, пока
  статьи нет в корпусе (Р17);
- книга ставок — день выкладки и даты замеров +90 и +180;
- база проекта — адаптером `records.adapter` (снимок «до» → план → `--apply` → чтение обратно), ступень
  `records.allow`; не подключён — «не подключено»;
- строка журнала действий проекта (`publish.journal_path`, `{yyyy-mm}` — месяц дня выкладки); пусто — «не подключено».
Отчёт — что вышло и запас очереди в днях.
"""
from __future__ import annotations

from pathlib import Path

from ..core import actions as actions_mod
from ..core import bets, locks, publications, rights, store
from ..core.errors import GateRefused, WriteUnconfirmed
from . import queue as queue_mod
from . import reservations

SOURCE = "запись выпуска"


def release_fields(instance, tid: str, ref: dict) -> dict:
    """Поля страницы для карты спроса — пишутся в событие «published» журнала выпуска."""
    from .brief import cluster_queries
    try:
        topic = queue_mod._find(queue_mod.load(instance), tid)
    except Exception:                                   # тема не из очереди (страница вне машины)
        topic = None
    queries = cluster_queries(instance, topic) if topic else []
    key = instance.norm.key(" ".join([ref["title"], ref.get("h1") or ""]))
    return {"slug": ref["slug"], "title": ref["title"], "key": key.to_list(),
            "cluster_id": topic.get("cluster_id") if topic else None, "target_keys": queries,
            "topic": topic["topic"] if topic else ""}


def _map_page(instance, row: dict) -> dict:
    return {"id": row["slug"], "url": row["url"], "type": "article", "title": row["title"], "corpus_id": "",
            "key": row["key"], "title_key": instance.norm.key(row["title"]).to_list(),
            "target_keys": list(row.get("target_keys") or []), "head_queries": [], "status": "live",
            "source": SOURCE, "cluster_id": row.get("cluster_id"), "value": None}


def add_to_map(instance, row: dict) -> tuple[bool, str]:
    path = instance.file("demand_map")
    if not path.is_file():
        return False, "карта спроса: не собрана — страница войдёт при `map build` из журнала выпуска"
    data = store.read_json(path, "demand_map")
    pages = data["records"]
    if any(p["id"] == row["slug"] for p in pages):
        return False, "карта спроса: страница уже есть"
    pages.append(_map_page(instance, row))
    clusters = data.get("clusters") or []
    for c in clusters:
        if c["id"] == row.get("cluster_id") and c["status"] in ("free", "reserved"):
            c.update(status="covered", owner=row["slug"], reservation=None)
    candidates = [c for c in data.get("candidates") or [] if c["cluster_id"] != row.get("cluster_id")]
    meta = {k: v for k, v in data.items() if k not in ("records", "schema_version", "kind")}
    meta.update(clusters=clusters, candidates=candidates)
    return True, store.write_json(path, "demand_map", pages, meta=meta)


def _journal(instance, tid: str, url: str, day: str) -> tuple[bool, str]:
    template = instance.p["publish"]["journal_path"]
    if not template:
        return False, "журнал действий: не подключено (publish.journal_path пуст)"
    path = Path(template.replace("{yyyy-mm}", day[:7]))
    path = path if path.is_absolute() else instance.root / path
    marker = f"content-engine: {tid} {url}"
    if path.is_file() and marker in path.read_text(encoding="utf-8"):
        return False, "журнал действий: строка уже есть"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (f"- {day[8:10]}.{day[5:7]} · Сайт: статья {url} · Контент-машина, выпуск темы {tid}: страница доехала, "
            f"записи дня выкладки сделаны. Проверка: `publish verify {tid} --url {url}` ({marker})\n")
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
    if line not in path.read_text(encoding="utf-8"):
        raise WriteUnconfirmed(f"журнал действий {path.name}: строка дописана — прочитана другой")
    return True, f"журнал действий: строка дописана в {path.name} и прочитана обратно"


def record(instance, tid: str, *, apply: bool = False) -> dict:
    """Записи под блокировкой темы: два прогона не создают две строки базы проекта."""
    with locks.held(instance, tid, "record"):
        return _record(instance, tid, apply=apply)


def _record(instance, tid: str, *, apply: bool = False) -> dict:
    first = publications.published(instance, tid=tid)
    if first is None:
        raise GateRefused(f"тема {tid}: «доехало» ещё не подтверждено — сначала `publish verify {tid}`")
    url, day = first["url"], first["day"]
    lines, new = [], 0
    topics = queue_mod.load(instance)
    topic = queue_mod._find(topics, tid)
    records = reservations.load(instance)
    reservation = next((r for r in records if r["id"] == topic.get("reservation_id")), None)
    if reservation is None:
        lines.append("бронь: у темы брони нет")
    elif reservation["status"] == reservations.CONVERTED:
        lines.append(f"бронь {reservation['id']}: уже страница")
    else:
        note = "" if reservation["status"] == reservations.ACTIVE else \
            f"бронь истекла {reservation['expires']}, пока писалась статья"
        reservation.update(status=reservations.CONVERTED, url=url, converted=day, note=note)
        lines.append(f"бронь {reservation['id']} → страница" + (f" ({note})" if note else "") + "; "
                     + reservations.save(instance, records))
        new += 1
    if topic["status"] == queue_mod.PUBLISHED:
        lines.append(f"тема {tid}: уже published")
    else:
        topic.update(status=queue_mod.PUBLISHED, published=day, url=url)
        lines.append(f"тема {tid} → published; " + queue_mod.save(instance, topics))
        new += 1
    if topic.get("type") == "refresh":           # правка страницы: страница уже в карте и книге (Т36.3)
        added, text = 0, "карта спроса: правка страницы — страница уже в карте"
        lines.append(text)
        added, text = bets.mark_refreshed(instance, topic, url, day)
    else:
        added, text = add_to_map(instance, first)
        new += added
        lines.append(text)
        added, text = bets.add(instance, topic, url, day)
    new += added
    lines.append(text)
    lines.append(_records_db(instance, tid, url, apply))
    lines.append(_transfer(instance, tid, url, apply))
    added, text = _journal(instance, tid, url, day)
    new += added
    lines.append(text)
    try:
        status, _ = queue_mod.status(instance)
        lines.append(f"запас очереди: {status['stock']} тем ≈ {status['days']} дн.")
    except Exception as exc:                            # пустая очередь не отменяет записи
        lines.append(f"запас очереди: {exc}")
    if new:
        publications.log(instance, "record", id=tid, url=url, new=new)
    return {"id": tid, "url": url, "day": day, "new": new, "lines": lines}


def _records_db(instance, tid: str, url: str, apply: bool) -> str:
    spec = instance.p["records"]["adapter"]
    if spec == "none":
        return "база проекта: не подключено (records.adapter: none)"
    from ..core.plugins import load_class
    adapter = load_class(instance, spec, {}, "records.adapter")(instance)
    if adapter.exists(tid, url):
        return "база проекта: строка уже есть"
    planned = adapter.plan(tid, url)
    allowed, refused = rights.split(instance, planned)
    out = [f"не в плане — {r}" for r in refused]
    if apply and allowed:
        result = actions_mod.run(instance, tid, allowed, apply=True)
        out.append(f"база проекта: {result.line()}")
        rollback = getattr(adapter, "rollback_note", None)
        if rollback:
            out.append(f"откат строки базы — вручную: {rollback(tid, url)}")
    else:
        actions_mod.run(instance, tid, allowed, apply=False, echo=lambda s: out.append(s.strip()))
    return "база проекта: " + "; ".join(out)


def _transfer(instance, tid: str, url: str, apply: bool) -> str:
    """Н1: текст и разметка — в папку выпущенной статьи проекта (ступень `paths.allow`, план без `--apply`)."""
    from ..core import transfer
    p = instance.p["paths"]
    if p["transfer"] == "builtin" and not p["published_dir"]:
        return "папка проекта: не подключено (paths.published_dir пуст) — статью в корпус проекта кладёт человек"
    try:
        adapter = transfer.load(instance)
        if adapter.exists(tid, url):
            return "папка проекта: статья уже лежит там с теми же файлами"
        planned = adapter.plan(tid, url)
    except GateRefused as exc:
        return f"папка проекта: ✗ {exc}"
    allowed, refused = rights.split(instance, planned)
    out = [f"не в плане — {r}" for r in refused]
    if apply and refused and not allowed:
        out.append("ОТКАЗ: передача с --apply не выполнена — нет права")
    if apply and allowed:
        result = actions_mod.run(instance, tid, allowed, apply=True, echo=lambda s: out.append(s.strip()))
        out.append(f"передача: {result.line()}; откат — `publish rollback {tid} --apply`")
    else:
        actions_mod.run(instance, tid, allowed, apply=False, echo=lambda s: out.append(s.strip()))
    return "папка проекта: " + "; ".join(out)


def run_records(instance, tid: str, *, apply: bool = False) -> int:
    out = record(instance, tid, apply=apply)
    print(f"Записи дня выкладки {tid} ({out['day']}, {out['url']}): новых {out['new']}")
    for line in out["lines"]:
        print(f"  {line}")
    return 1 if apply and any("ОТКАЗ:" in line for line in out["lines"]) else 0


def release_pages(instance, known: set[str]) -> list[dict]:
    """Страницы из записей выпуска, которых нет среди страниц корпуса, — для `map build` (Т23, Р17)."""
    out = []
    for row in publications.read(instance):
        if row["event"] == "published" and row.get("slug") and row["slug"] not in known:
            out.append(_map_page(instance, row))
            known.add(row["slug"])
    return out

