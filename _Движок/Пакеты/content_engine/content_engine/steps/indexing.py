"""Индексация (Г3, Т24): `content-engine index submit <id> [--apply] | status`.

`submit` — заявки о новом адресе: IndexNow (Яндекс и Bing; до отправки — файл ключа на сайте), переобход Вебмастера
(предел суток — до отправки), Google — только проверка, что адрес есть в карте сайта: мгновенного уведомления для
статей у Google нет, отдельный индексатор не используется. Это запись вовне: ступень `indexing.allow = write`,
`--apply`, журнал `data/publications.jsonl`. Один адрес — не больше одной заявки в каждую систему за сутки.

`status` — по каждому новому адресу на дни `indexing.status_days` от выкладки: когда отправлен, в индексе ли Google
(проверка адреса в Search Console) и Яндекс (адреса в поиске Вебмастера); источник выключен или недоступен —
«не подключено». Не в индексе к последнему дню (14-му) — замечание.
"""
from __future__ import annotations

import json
from datetime import date

from ..core import actions as actions_mod
from ..core import publications, rights
from ..core.env_file import secret
from ..core.errors import GateRefused, RightsRefused, SourceError, WriteUnconfirmed
from ..core.urls import norm_url, slug_of

OFF = "не подключено"


def _sent_today(instance, url: str) -> set[str]:
    today = instance.today.isoformat()
    return {row["system"] for row in publications.read(instance)
            if row["event"] == "indexing" and row.get("url") == norm_url(url) and row.get("day") == today}


def submit(instance, tid: str, *, apply: bool, transport=None) -> dict:
    """Заявки под блокировкой темы: два прогона не видят «сегодня не отправлено» одновременно."""
    from ..core import locks
    with locks.held(instance, tid, "index"):
        return _submit(instance, tid, apply=apply, transport=transport)


def _submit(instance, tid: str, *, apply: bool, transport=None) -> dict:
    first = publications.published(instance, tid=tid)
    if first is None:
        raise GateRefused(f"тема {tid}: «доехало» не подтверждено — заявки на индексацию не отправляются")
    url = "https://" + first["url"]
    ix = instance.p["indexing"]
    lines, planned = [], []
    sent = _sent_today(instance, url)
    if ix["indexnow"]["enabled"]:
        from ..adapters.indexnow import key_file_ok, submit_actions
        try:
            key = secret(instance.p, ix["indexnow"]["key_env"])
        except SourceError as exc:
            key = None
            lines.append(f"IndexNow: {OFF} — {exc}")
        if key:
            ok, detail = key_file_ok(instance, key, transport=transport)
            if not ok:
                lines.append(f"IndexNow: {OFF} — {detail}")
            else:
                for action in submit_actions(instance, url, key, transport=transport):
                    if action.meta["system"] in sent:
                        lines.append(f"{action.meta['system']}: сегодня уже отправлено — повтор не идёт")
                    else:
                        planned.append(action)
    else:
        lines.append(f"IndexNow: {OFF} (indexing.indexnow.enabled: false)")
    if ix["webmaster"]["enabled"] and instance.p["sources"]["webmaster"]["enabled"]:
        from ..adapters.webmaster_write import WebmasterWrite
        if "webmaster" in sent:
            lines.append("Вебмастер: сегодня уже отправлено — повтор не идёт")
        else:
            try:
                wm = WebmasterWrite(instance, transport=transport)
                left = wm.quota()
                if left < 1:
                    lines.append("Вебмастер: суточный предел переобхода выбран — заявка не отправлена (считано до "
                                 "отправки)")
                else:
                    lines.append(f"Вебмастер: остаток переобхода на сутки {left}")
                    planned.append(wm.recrawl_action(url))
            except SourceError as exc:
                lines.append(f"Вебмастер: {OFF} — {exc}")
    else:
        lines.append(f"Вебмастер: {OFF} (indexing.webmaster.enabled или sources.webmaster.enabled — false)")
    lines.append(_google_sitemap(instance, url, transport))
    allowed, refused = rights.split(instance, planned)
    lines += [f"не в плане — {r}" for r in refused]
    if apply and planned and not allowed:
        raise RightsRefused(f"заявки на индексацию не отправлены: indexing.allow = {rights.level(instance, 'indexing')}")
    failed = None
    try:
        out = actions_mod.run(instance, tid, allowed, apply=apply, echo=lambda s: lines.append(s.strip())) \
            if allowed else actions_mod.Outcome(planned=0)
    except WriteUnconfirmed as exc:
        out, failed = exc.outcome, exc
    if apply:
        for action in allowed:
            ok = any(d.startswith("✓") and action.kind in d for d in out.details)
            publications.log(instance, "indexing", id=tid, url=norm_url(url), system=action.meta["system"],
                             day=instance.today.isoformat(), confirmed=ok,
                             answer=next((d for d in out.details if action.kind in d), ""))
    if failed:
        raise failed
    return {"url": url, "lines": lines, "written": out.written, "confirmed": out.confirmed}


def _google_sitemap(instance, url: str, transport) -> str:
    from ..adapters.corpus import sitemap_urls
    try:
        urls = sitemap_urls(instance.p, instance.root, transport=transport if transport is not None
                            else instance.transport)
    except SourceError as exc:
        return f"Google: карта сайта не прочитана — {exc}"
    if not urls:
        return f"Google: {OFF} — карта сайта не задана (site.sitemap_url или sitemap_file)"
    there = norm_url(url) in set(urls)
    return "Google: адрес есть в карте сайта" if there else "Google: адреса в карте сайта пока нет — проверить позже"


def status(instance, *, transport=None, all_days: bool = False) -> dict:
    days = instance.p["indexing"]["status_days"]
    rows = [r for r in publications.read(instance) if r["event"] == "published"]
    sends = [r for r in publications.read(instance) if r["event"] == "indexing"]
    google = yandex = None
    lines, notes = [], []
    for row in rows:
        age = (instance.today - date.fromisoformat(row["day"])).days
        if not all_days and age not in days:
            continue
        url = "https://" + row["url"]
        sent = ", ".join(f"{s['system']} {s['day']} ({'подтверждено' if s.get('confirmed') else 'не подтверждено'})"
                         for s in sends if s.get("url") == row["url"]) or "заявок нет"
        g = y = OFF
        if instance.p["sources"]["gsc"]["enabled"]:
            try:
                if google is None:
                    from ..adapters.gsc_inspect import UrlInspection
                    google = UrlInspection.from_profile(instance)
                res = google.inspect(url)
                g = ("в индексе" if res["indexed"] else "нет") + f" ({res['coverage'] or res['verdict']})"
            except (SourceError, OSError, ValueError) as exc:
                g = f"{OFF} — {exc}"
        if instance.p["indexing"]["webmaster"]["enabled"] and instance.p["sources"]["webmaster"]["enabled"]:
            try:
                if yandex is None:
                    from ..adapters.webmaster_write import WebmasterWrite
                    yandex = WebmasterWrite(instance, transport=transport)
                found = yandex.in_search(url)
                y = "в поиске" if found else ("нет" if found is False else "не дочитано")
            except SourceError as exc:
                y = f"{OFF} — {exc}"
        lines.append(f"{row['id']} {url}: день {age} от выкладки {row['day']}; заявки: {sent}; Google: {g}; "
                     f"Яндекс: {y}")
        if age >= max(days) and (g.startswith("нет") or y.startswith("нет")):
            notes.append(f"{row['id']} {slug_of(url)}: не в индексе к {age}-му дню — разобрать")
    return {"lines": lines, "notes": notes}


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("index", help="индексация: submit | status")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("submit", help="заявки IndexNow и переобход Вебмастера: без --apply — план")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("status", help="в индексе ли новые адреса на дни indexing.status_days")
    add_profile(p)
    p.add_argument("--all", action="store_true", help="все выпущенные адреса, а не только в дни замера")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "submit":
        out = submit(instance, args.id, apply=args.apply)
        print(f"Индексация {args.id}: {out['url']}")
        for line in out["lines"]:
            print(f"  {line}")
        return 0
    out = status(instance, all_days=args.all)
    text = "\n".join(["Статус индексации"] + [f"  {x}" for x in out["lines"] or ["в дни замера адресов нет"]]
                     + [f"  ! {n}" for n in out["notes"]])
    (instance.reports_dir() / "Индексация.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    (instance.reports_dir() / "Индексация.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                            encoding="utf-8", newline="\n")
    print(text)
    return 0
