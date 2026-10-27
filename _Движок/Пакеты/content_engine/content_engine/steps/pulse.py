"""Пульс и панель (модуль Д1, Т34): `content-engine pulse run | notify`.

`pulse run` собирает блоки дня — сайт отвечает, публикации против ритма, запас тем в днях, трафик Яндекса и Google
раздельно, позиции своих ключей и тревоги, дрифт выложенных страниц, свежесть аудита, индексация новых адресов, цена
статьи за 30 дней, ближайшие замеры ставок — и выносит вердикт по правилам (`core/verdict.py`). Упавший источник не
мешает остальным блокам; блок без источника пишет «не подключено» и как подключить, а не ноль. Итог —
`data/pulse/<дата>.json` и панель `reports/<дата>/panel.html` (витрина без кнопок: задания — текстом рядом с проблемой).

`pulse notify` отправляет только разницу со вчера и что сделать (Т34.5): без `--apply` — план, с `--apply` — по праву
`notify.allow: write`, подтверждение — код ответа сервиса; второй отправки за день нет.
"""
from __future__ import annotations

import html
import json
from datetime import date, timedelta
from importlib import resources

from ..core import audit_store, drift, positions, publications, store
from ..core.errors import EngineError, NetworkUnreachable, SourceError
from ..core.verdict import decide

NOT_CONNECTED = "не подключено"


def _folder(instance):
    return instance.root / "data" / "pulse"


def previous(instance) -> dict | None:
    folder = _folder(instance)
    days = sorted(p for p in folder.glob("????-??-??.json") if p.stem < instance.today.isoformat()) \
        if folder.is_dir() else []
    return json.loads(days[-1].read_text(encoding="utf-8")) if days else None


def _site(instance, transport, last_url):
    from ..adapters.page_fetch import PageFetcher
    fetcher = PageFetcher.for_own_site(instance, transport=transport)
    urls = [f"https://{instance.p['site']['domains'][0]}/"] + ([last_url] if last_url else [])
    checked = []
    for url in urls:
        try:
            got = fetcher.get(url, allow_redirects=True)
        except NetworkUnreachable as exc:
            return {"ok": False, "why": f"{url}: {exc}", "checked": checked}
        except SourceError as exc:
            return {"ok": False, "why": f"{url}: {exc}", "checked": checked}
        checked.append(f"{url}: HTTP {got.status}")
        if got.status != 200:
            return {"ok": False, "why": f"{url}: HTTP {got.status}", "checked": checked}
    return {"ok": True, "why": "", "checked": checked}


def _publications(instance):
    rows = [r for r in publications.read(instance) if r["event"] == "published"]
    since = (instance.today - timedelta(days=6)).isoformat()
    per_day = instance.p["queue"]["per_day"]
    last = max((r["day"] for r in rows), default=None)
    return {"last_day": last, "last_url": ("https://" + max(rows, key=lambda r: r["day"])["url"]) if rows else "",
            "week": sum(1 for r in rows if r["day"] >= since), "plan_week": round(per_day * 7, 1)}


def _index(instance):
    """Новые адреса не в индексе к `index_days_alarm` дню — по записям `index status` (без сети)."""
    alarm = instance.p["monitor"]["index_days_alarm"]
    rows = publications.read(instance)
    last = {}
    for r in rows:
        if r["event"] == "index_status":
            last[r["url"]] = r
    notes = []
    for r in rows:
        if r["event"] != "published":
            continue
        age = (instance.today - date.fromisoformat(r["day"])).days
        st = last.get(r["url"])
        if age >= alarm and (st is None or not (st.get("google_in") and st.get("yandex_in"))):
            notes.append(f"{r.get('id')} {r['url']}: {age} дн. от выкладки — "
                         + ("статус индексации не снимался (`index status`)" if st is None else "не во всех индексах"))
    return notes


def _stock(instance):
    from . import queue as queue_mod
    try:
        out, _ = queue_mod.status(instance)
        return {"stock": out["stock"], "days": out["days"], "norm": out["norm"]}
    except EngineError as exc:
        return {"error": str(exc)}


def _spend(instance):
    from ..core import spend
    data = spend.report(instance, 30)
    return {"text": spend.render(data)}


def _bets(instance):
    from ..core import bets
    soon = []
    for b in bets.load(instance):
        for window, day in (b.get("measure") or {}).items():
            if date.fromisoformat(day) >= instance.today:
                soon.append({"day": day, "window": window, "id": b["id"], "url": b.get("url", ""),
                             "hypothesis_id": b.get("hypothesis_id") or ""})
    return sorted(soon, key=lambda x: x["day"])[:10]


def collect(instance, *, transport=None, network: bool = True) -> dict:
    transport = transport if transport is not None else instance.transport
    pub = _publications(instance)
    blocks = {"publications": pub, "stock": _stock(instance), "spend": _spend(instance), "bets": _bets(instance),
              "index": _index(instance)}
    audit = audit_store.latest(instance)
    blocks["audit"] = {"date": audit["date"] if audit else None, "age": audit_store.age_days(instance, audit),
                       "critical": sum(1 for f in audit["findings"] if f["level"] == "critical") if audit else None}
    if network:
        blocks["site"] = _site(instance, transport, pub["last_url"])
        snap = positions.snapshot(instance, transport=transport)
        before = positions.previous(instance)
        blocks["positions"] = {"engines": snap["engines"], "keys": snap["keys"], "note": snap.get("keys_note", ""),
                               "alarms": positions.alarms(instance, snap["rows"], before),
                               "write": positions.save(instance, snap)}
        corpus_urls = [pg.url for pg in instance.corpus()[0]] if instance.p["corpus"]["folders"] else []
        try:
            blocks["drift"] = drift.check(instance, transport=transport, extra_urls=corpus_urls)
        except (SourceError, OSError) as exc:
            blocks["drift"] = {"error": str(exc), "findings": [], "critical": None}
    else:
        blocks["site"] = {"ok": None, "why": "--no-network"}
        blocks["positions"] = {"engines": {}, "alarms": [], "note": "не проверялось: --no-network"}
        blocks["drift"] = {"findings": [], "critical": None, "note": "не проверялось: --no-network"}
    unchecked = []
    dr = blocks["drift"]
    blind = sum(1 for f in dr.get("findings", []) if f["level"] == drift.UNVERIFIED)
    if blind:
        unchecked.append(f"дрифт: сеть не ответила у {blind} стр.")
    for name, e in (blocks["positions"].get("engines") or {}).items():
        if e.get("status") not in ("ок", "не подключено"):
            unchecked.append(f"позиции {name}: {e.get('status')}")
    facts = {"unchecked": unchecked, "site_ok": blocks["site"]["ok"], "site_why": blocks["site"]["why"], "last_publication": pub["last_day"],
             "drift_critical": blocks["drift"].get("critical"), "audit_critical": blocks["audit"]["critical"],
             "audit_age": blocks["audit"]["age"], "stock_days": blocks["stock"].get("days")}
    verdict = decide(instance, facts)
    data = {"schema_version": 1, "kind": "pulse", "date": instance.today.isoformat(), "verdict": verdict,
            "blocks": blocks}
    data["diff"] = diff(previous(instance), data)
    return data


def diff(old: dict | None, new: dict) -> list[str]:
    """Разница со вчера: только то, что изменилось (Т34.5)."""
    if old is None:
        return [f"первый пульс: {new['verdict']['color']} — {new['verdict']['why']}"]
    out = []
    if old["verdict"]["color"] != new["verdict"]["color"]:
        out.append(f"вердикт: {old['verdict']['color']} → {new['verdict']['color']} — {new['verdict']['why']}")
    for a in new["blocks"]["positions"].get("alarms", []):
        out.append(f"{a['engine']}: {a['kind']} «{a['query']}» {a['was']} → {a['now']}")
    seen = {(f["url"], f["what"]) for f in old["blocks"]["drift"].get("findings", [])}
    for f in new["blocks"]["drift"].get("findings", []):
        if (f["url"], f["what"]) not in seen and f["level"] != drift.UNVERIFIED:
            out.append(f"дрифт ({f['level']}): {f['url']} — {f['what']}")
    o, n = old["blocks"]["stock"].get("days"), new["blocks"]["stock"].get("days")
    if o is not None and n is not None and o != n:
        out.append(f"запас: {o} → {n} дн.")
    if old["blocks"]["publications"]["last_day"] != new["blocks"]["publications"]["last_day"]:
        out.append(f"новая публикация: {new['blocks']['publications']['last_day']}")
    return out


def save(instance, data: dict) -> str:
    path = _folder(instance) / f"{instance.today.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    path.write_text(text, encoding="utf-8", newline="\n")
    if json.loads(path.read_text(encoding="utf-8"))["date"] != data["date"]:
        raise EngineError(f"{path.name}: снимок пульса не прочитался обратно")
    return str(path)


def _e(x) -> str:
    return html.escape(str(x))


def panel(instance, data: dict) -> str:
    """Панель — витрина без кнопок. Каждое число — с периодом или источником; без данных — «не подключено»."""
    b, v = data["blocks"], data["verdict"]
    rows = []

    def block(title, body):
        rows.append(f"<section><h2>{_e(title)}</h2>{body}</section>")

    pub = b["publications"]
    block("Публикации", f"<p>за 7 дней: {pub['week']} при плане {pub['plan_week']}; последняя — "
                        f"{_e(pub['last_day'] or 'нет в журнале выпуска')}</p>")
    st = b["stock"]
    block("Запас тем", f"<p>{_e(st['error'])}</p>" if "error" in st else
          f"<p>{st['stock']} тем ≈ {st['days']} дн. (норма {st['norm']})</p>")
    eng = b["positions"].get("engines") or {}
    lines = []
    for name, title in (("yandex", "Яндекс"), ("google", "Google")):
        e = eng.get(name)
        if not e:
            lines.append(f"<li>{title}: {_e(b['positions'].get('note') or NOT_CONNECTED)}</li>")
        elif e["status"] != "ок":
            lines.append(f"<li>{title}: {_e(e['status'])} — {_e(e.get('why', ''))}</li>")
        else:
            t = e.get("traffic") or {}
            lines.append(f"<li>{title}: клики {t.get('clicks')}, показы {t.get('impressions')} за {_e(t.get('period'))}"
                         f" ({_e(t.get('status'))}, {_e(t.get('source'))}); позиций своих ключей — {e['rows']}</li>")
    block("Трафик и позиции", "<ul>" + "".join(lines) + "</ul>" + "".join(
        f"<p>тревога {_e(a['engine'])}: {_e(a['kind'])} «{_e(a['query'])}» {a['was']} → {a['now']}</p>"
        for a in b["positions"].get("alarms", [])))
    dr = b["drift"]
    block("Дрифт", f"<p>{_e(dr.get('note') or dr.get('error') or '')}</p>" + "".join(
        f"<p>{_e(f['level'])}: {_e(f['url'])} — {_e(f['what'])} ({_e(f['was'])} → {_e(f['now'])})</p>"
        for f in dr.get("findings", [])) + (f"<p>эталон снят у {len(dr.get('baselined', []))} страниц</p>"
                                             if dr.get("baselined") else ""))
    au = b["audit"]
    if au["date"] is None:
        audit_text = "аудита нет — `content-engine audit run`"
    else:
        audit_text = f"последний {au['date']}, {au['age']} дн. назад, критичных находок {au['critical']}"
    block("Аудит", f"<p>{_e(audit_text)}</p>")
    block("Индексация", "".join(f"<p>{_e(n)}</p>" for n in b["index"]) or "<p>замечаний нет</p>")
    block("Цена статей за 30 дней", f"<pre>{_e(b['spend']['text'])}</pre>")
    block("Ближайшие замеры ставок", "".join(f"<p>{_e(x['day'])} — {_e(x['id'])} (+{_e(x['window'])})</p>"
                                              for x in b["bets"]) or "<p>нет</p>")
    template = resources.files("content_engine.templates").joinpath("panel.html").read_text(encoding="utf-8")
    color = {"красный": "red", "жёлтый": "yellow", "зелёный": "green"}[v["color"]]
    return (template.replace("{{date}}", _e(data["date"])).replace("{{color}}", color)
            .replace("{{verdict}}", _e(v["color"].upper())).replace("{{why}}", _e(v["why"]))
            .replace("{{todo}}", _e(v["todo"] or "—"))
            .replace("{{unchecked}}", _e("; ".join(v["unchecked"]) or "—"))
            .replace("{{diff}}", "".join(f"<li>{_e(d)}</li>" for d in data["diff"]) or "<li>изменений нет</li>")
            .replace("{{blocks}}", "\n".join(rows)))


def summary(data: dict) -> str:
    v = data["verdict"]
    lines = [f"Пульс {data['date']}: {v['color'].upper()} — {v['why']}"]
    lines += [f"• {d}" for d in data["diff"]] or ["• изменений со вчера нет"]
    if v.get("unchecked"):
        lines.append("Не проверено: " + "; ".join(v["unchecked"]))
    if v["todo"]:
        lines.append(f"Что сделать: {v['todo']}")
    return "\n".join(lines)


def run_pulse(instance, *, network: bool = True, transport=None) -> int:
    data = collect(instance, transport=transport, network=network)
    path = save(instance, data)
    page = instance.reports_dir() / "panel.html"
    page.write_text(panel(instance, data), encoding="utf-8", newline="\n")
    print(summary(data))
    print(f"снимок: {path}\nпанель: {page}")
    return 0


def run_notify(instance, *, apply: bool, transport=None) -> int:
    from ..adapters import notify
    from ..core import actions as actions_mod
    from ..core import rights
    path = _folder(instance) / f"{instance.today.isoformat()}.json"
    if not path.is_file():
        raise EngineError("пульса за сегодня нет — сначала `content-engine pulse run`")
    data = json.loads(path.read_text(encoding="utf-8"))
    text = summary(data)
    if any(r["event"] == "notify" and r.get("day") == instance.today.isoformat() and r.get("confirmed")
           for r in publications.read(instance)):
        print("сводка за сегодня уже отправлена — повтор не нужен")
        return 0
    act = notify.action(instance, text, transport=transport)
    if act is None:
        print(f"сводка: {NOT_CONNECTED} (notify.kind: none) — текст:\n{text}")
        return 0
    allowed, refused = rights.split(instance, [act])
    for line in refused:
        print(f"  не в плане — {line}")
    print(f"текст сводки:\n{text}")
    if apply and not allowed:
        from ..core.errors import RightsRefused
        raise RightsRefused("сводка не отправлена: нужно разрешение notify.allow = write")
    planned = allowed if apply else [act]          # план показывает отправку и при ступени files
    out = actions_mod.run(instance, f"pulse-{instance.today.isoformat()}", planned, apply=apply)
    if apply:
        publications.log(instance, "notify", day=instance.today.isoformat(), kind=act.kind,
                         confirmed=out.confirmed == out.written and out.written > 0)
    return 0


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("pulse", help="пульс и панель: run — вердикт дня и панель; notify — сводка в мессенджер")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("run", help="вердикт дня, панель reports/<дата>/panel.html, снимок data/pulse/<дата>.json")
    add_profile(p)
    p.add_argument("--no-network", action="store_true", help="без обращений к сайту и консолям")
    p.add_argument("--unattended", action="store_true", help="запуск без человека (расписание); платных вызовов нет")
    p.set_defaults(func=run)
    p = actions.add_parser("notify", help="сводка пульса в мессенджер: без --apply — план")
    add_profile(p)
    p.add_argument("--apply", action="store_true", help="отправить (нужно notify.allow: write)")
    p.add_argument("--unattended", action="store_true", help="запуск без человека: только с правом и --apply")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "run":
        return run_pulse(instance, network=not args.no_network)
    return run_notify(instance, apply=args.apply)
