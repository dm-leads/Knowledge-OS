"""Брони кластеров (Т11.5, Т12.4).

Бронь ставит гейт темы с вердиктом «свободно» и флагом `--reserve` (или пополнение очереди) на
`thresholds.reservation_days`. Пока бронь активна, тот же кластер и тема с тем же ключом получают на слое 1
«пересекается: забронирован темой …». Истёкшая бронь освобождается при запуске любой команды, а её тема
возвращается в `candidate` и снова проходит гейт. `reserve convert --url` — бронь стала страницей (до волны 2 руками).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..core import store
from ..core.errors import EngineError

ACTIVE, RELEASED, EXPIRED, CONVERTED = "active", "released", "expired", "converted"


def load(instance) -> list[dict]:
    return store.read_json(instance.file("reservations"), "reservations", missing_ok=True)["records"]


def save(instance, records: list[dict]) -> str:
    return store.write_json(instance.file("reservations"), "reservations", records)


def active(instance) -> list[dict]:
    return [r for r in load(instance) if r["status"] == ACTIVE]


def expire(instance) -> int:
    """Освободить истёкшие брони; темы очереди с такой бронью — обратно в `candidate`."""
    path = instance.file("reservations")
    if not path.is_file():
        return 0
    records = load(instance)
    today = instance.today.isoformat()
    gone = [r for r in records if r["status"] == ACTIVE and r["expires"] < today]
    if not gone:
        return 0
    for record in gone:
        record["status"] = EXPIRED
    save(instance, records)
    from . import queue as queue_mod
    ids = {r["id"] for r in gone}
    topics = queue_mod.load(instance)
    changed = False
    for topic in topics:
        if topic.get("reservation_id") in ids and topic["status"] == queue_mod.GATED:
            topic.update(status=queue_mod.CANDIDATE, reservation_id=None, gate=None)
            changed = True
    if changed:
        queue_mod.save(instance, topics)
    return len(gone)


def create(instance, *, topic: str, key: list, cluster_id: str | None, who: str, queue_id: str | None = None) -> dict:
    records = load(instance)
    days = instance.p["thresholds"]["reservation_days"]
    record = {"id": f"R{len(records) + 1:04d}", "cluster_id": cluster_id, "topic": topic, "key": key, "who": who,
              "queue_id": queue_id, "created": instance.today.isoformat(),
              "expires": (instance.today + timedelta(days=days)).isoformat(), "status": ACTIVE, "url": ""}
    records.append(record)
    save(instance, records)
    return record


def _set(instance, rid: str, **changes) -> dict:
    records = load(instance)
    for record in records:
        if record["id"] == rid:
            if record["status"] != ACTIVE:
                raise EngineError(f"бронь {rid} не активна ({record['status']})")
            record.update(changes)
            save(instance, records)
            return record
    raise EngineError(f"брони {rid} нет")


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("reserve", help="брони кластеров: list | release | convert")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("list", "release", "convert"):
        p = actions.add_parser(name)
        add_profile(p)
        if name != "list":
            p.add_argument("id")
        if name == "convert":
            p.add_argument("--url", required=True, help="адрес опубликованной страницы")
        p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "list":
        rows = load(instance)
        for r in rows:
            print(f"{r['id']}  {r['status']:<9} до {r['expires']}  кластер {r['cluster_id'] or '—'}  «{r['topic']}»"
                  + (f"  → {r['url']}" if r["url"] else ""))
        print(f"броней: {len(rows)}, активных: {sum(r['status'] == ACTIVE for r in rows)}")
        return 0
    if args.action == "release":
        record = _set(instance, args.id, status=RELEASED)
        _return_topic(instance, record)
    else:
        record = _set(instance, args.id, status=CONVERTED, url=args.url)
    print(f"бронь {record['id']}: {record['status']}")
    return 0


def _return_topic(instance, record: dict):
    """Бронь снята — её тема очереди снова кандидат и проходит гейт заново."""
    from . import queue as queue_mod
    topics = queue_mod.load(instance)
    for topic in topics:
        if topic.get("reservation_id") == record["id"] and topic["status"] == queue_mod.GATED:
            topic.update(status=queue_mod.CANDIDATE, reservation_id=None, gate=None)
            queue_mod.save(instance, topics)
            return


def is_active_on(record: dict, day: date) -> bool:
    return record["status"] == ACTIVE and record["expires"] >= day.isoformat()
