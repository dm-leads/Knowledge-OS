"""Подготовка статьи (В1, В2; Т18–Т19): `content-engine prepare top | donors | brief | check | approve`.

`top <id>` — разбор топа в числа. Тема только `taken`; выдача — снимок дня взятия `serp.json` (Т18.2), запросов к
источнику выдачи нет; снимок старше `prepare.serp_max_age_days` — отказ с подсказкой `queue retake`. Отбор (Т18.3):
топ-10 головной формулировки, добор из остальных по порядку; минус свои домены, коммерческие домены и адреса по
`prepare.skip_url_patterns`; первые `prepare.top_n` страниц, ответивших 200 и разобранных. Страница без текста без
скриптов — «не разобрано» с причиной, в числа не входит и место в топе не занимает. Три числа (Т18.6): объём по
`volume_rule` в коридоре направления, картинки — наибольшее × (1 + запас) вверх до целого, таблица — по `table_rule`.
Пробелы (Т18.7) — заголовки H2–H3 конкурентов, большая часть основ которых не встречается в заголовках разделов наших
статей по теме (верхние 5 статей и разделов слоя 2). Фактура (Т18.8) — «не подключено», пока нет библиотеки фактов.
Доноры (Т18.9) — только тезисы: заголовки и предложения с числами, у каждого — адрес-источник; текст не сохраняется.

`donors <id> --terms …` — адреса страниц доноров из их карт сайта по совпадению основ slug с формулировками темы на
языке донора (Snowball english); `--donor-url` — явный адрес, только с домена из `prepare.donors`.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date

import snowballstemmer

from ..adapters.page_fetch import PageFetcher
from ..core import store
from ..core.errors import EngineError, GateRefused, SourceError
from ..core.htmldoc import HtmlDoc
from ..core.number import FACT, Number
from ..core.urls import host_of, host_unicode, is_own, norm_url, slug_of
from . import queue as queue_mod

LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
GAP_SHARE = 0.5                 # заголовок — пробел, если больше половины его основ нет в наших заголовках
_EN = snowballstemmer.stemmer("english")


def topic_of(instance, tid: str, *, status: str = queue_mod.TAKEN) -> dict:
    topic = queue_mod._find(queue_mod.load(instance), tid)
    if topic["status"] != status:
        raise GateRefused(f"тема {tid} в статусе {topic['status']}: подготовка идёт только у темы {status} — "
                          f"`queue take {tid}`")
    return topic


def serp_snapshot(instance, tid: str) -> dict:
    path = instance.brief_dir(tid) / "serp.json"
    if not path.is_file():
        raise GateRefused(f"тема {tid}: снимка выдачи дня взятия нет — `queue retake {tid}`")
    data = store.read_json(path, "topic_serp")
    age = (instance.today - date.fromisoformat(data["as_of"])).days
    limit = instance.p["prepare"]["serp_max_age_days"]
    if age > limit:
        raise GateRefused(f"снимок выдачи темы {tid} от {data['as_of']} старше {limit} дн. — "
                          f"`queue retake {tid}` возьмёт свежую выдачу")
    return data


def candidates(instance, snapshot: dict) -> tuple[list[str], list[dict]]:
    """Адреса для разбора по порядку и выкинутые с причиной (Т18.3)."""
    p = instance.p
    shops = [host_unicode(d) for d in p["search"]["commercial_domains"]]
    patterns = [re.compile(x) for x in p["prepare"]["skip_url_patterns"]]
    by_phrase: dict[str, list[tuple[str, int]]] = {}
    for r in snapshot["records"]:
        by_phrase.setdefault(r["phrase"], []).extend((u, int(pos)) for u, pos in r["top"])
    order, dropped, seen = [], [], set()
    for phrase in snapshot["formulations"]:
        for url, _ in sorted(by_phrase.get(phrase, []), key=lambda x: x[1]):
            normal = norm_url(url)
            if normal in seen:
                continue
            seen.add(normal)
            host = host_of(normal)
            if is_own(normal, p["site"]["domains"]):
                dropped.append({"url": normal, "why": "свой домен"})
            elif any(host == s or host.endswith("." + s) for s in shops):
                dropped.append({"url": normal, "why": "коммерческий домен"})
            elif any(rx.search(normal) for rx in patterns):
                dropped.append({"url": normal, "why": "не статья (prepare.skip_url_patterns)"})
            else:
                order.append(normal)
    return order, dropped


def _fetcher(instance, urls) -> PageFetcher:
    pr = instance.p["prepare"]
    return PageFetcher(allowed_urls=urls, timeout=pr["page_timeout"], max_kb=pr["page_max_kb"],
                       cache_dir=instance.root / "cache" / "pages", cache_days=pr["page_cache_days"],
                       today=instance.today, transport=instance.transport)


def _direction(instance, name: str) -> dict | None:
    return next((d for d in instance.p["directions"] if d["name"] == name), None)


def _our_headings(instance, formulations: list[str]) -> list:
    """Заголовки разделов наших статей по теме: верхние 5 статей и разделов слоя 2 (Т18.7)."""
    from .dedup import layer_texts
    result = layer_texts(instance.index(), formulations, instance.p["thresholds"])
    slugs = {h["slug"] for h in result.numbers.get("body_top", [])[:5]} | \
            {h["slug"] for h in result.numbers.get("section_top", [])[:5]}
    heads = []
    for item in instance.index().pages:
        if item.page.slug in slugs:
            heads += [item.page.title, *item.page.headings]
    return sorted(slugs), heads


def gaps(instance, pages: list[dict], formulations: list[str]) -> tuple[list[dict], list[str]]:
    slugs, heads = _our_headings(instance, formulations)
    ours = set()
    for h in heads:
        ours |= instance.norm.key(h).variants
    out, seen = [], set()
    for page in pages:
        for heading in page["h2"] + page["h3"]:
            key = instance.norm.key(heading)
            if not key or key.canonical() in seen:
                continue
            seen.add(key.canonical())
            found = sum(1 for part in key.parts if part & ours) / len(key.parts)
            if found < GAP_SHARE:
                out.append({"heading": heading, "source": page["url"], "found_share": round(found, 2)})
    return out, slugs


def _theses(doc: HtmlDoc, url: str) -> list[dict]:
    m = doc.markers()
    out = [{"kind": "заголовок", "text": h, "source": url} for h in m["h2"] + m["h3"]]
    for sentence in re.split(r"(?<=[.!?])\s+", m["text"]):
        sentence = sentence.strip()
        if re.search(r"\d", sentence) and 20 <= len(sentence) <= 300:
            out.append({"kind": "число", "text": sentence, "source": url})
    return out


def top(instance, tid: str) -> dict:
    topic = topic_of(instance, tid)
    snapshot = serp_snapshot(instance, tid)
    pr = instance.p["prepare"]
    order, dropped = candidates(instance, snapshot)
    donor_urls = _donor_urls(instance, tid)
    all_serp = {norm_url(u) for r in snapshot["records"] for u, _ in r["top"]}
    fetcher = _fetcher(instance, all_serp | set(donor_urls))
    pages, skipped = [], []
    for url in order:
        if len(pages) >= pr["top_n"]:
            break
        try:
            got = fetcher.get(url, use_cache=True)
        except SourceError as exc:
            skipped.append({"url": url, "why": str(exc)})
            continue
        if got.status != 200:
            skipped.append({"url": url, "why": f"HTTP {got.status}" + (f" → {got.location}" if got.location else "")})
            continue
        doc = HtmlDoc(got.text, pr["main_blocks"])
        m = doc.markers()
        if m["chars"] == 0:
            skipped.append({"url": url, "why": "не разобрано: без скриптов текста нет"})
            continue
        pages.append({"url": url, "fetched": instance.today.isoformat(), "main": m["main"], "h2": m["h2"],
                      "h3": m["h3"], "chars": m["chars"], "tables": m["tables"], "images": m["images"],
                      "lists": m["lists"], "faq": m["faq"], "stems": _top_stems(instance, m["text"])})
    complete = len(pages) >= pr["top_n"]
    numbers = _numbers(instance, topic, pages) if pages else {}
    gap_list, our = gaps(instance, pages, topic["formulations"]) if pages else ([], [])
    donors = []
    for url in donor_urls:
        try:
            got = fetcher.get(url, use_cache=True)
        except SourceError as exc:
            donors.append({"url": url, "status": "не загружено", "why": str(exc), "theses": []})
            continue
        if got.status != 200:
            donors.append({"url": url, "status": "не загружено", "why": f"HTTP {got.status}", "theses": []})
            continue
        donors.append({"url": url, "status": "разобрано", "theses": _theses(HtmlDoc(got.text, pr["main_blocks"]),
                                                                            url)})
    report = {"id": tid, "topic": topic["topic"], "serp_as_of": snapshot["as_of"], "date": instance.today.isoformat(),
              "complete": complete,
              "incomplete_reason": "" if complete else f"разобрано {len(pages)} из {pr['top_n']}: "
                                                       "не хватило страниц, ответивших 200 с текстом",
              "numbers": numbers, "gaps": gap_list, "our_pages": our,
              "facts": {"status": "не подключено", "note": "библиотеки фактов в части 1 нет (Т18.8)"},
              "dropped": dropped, "skipped": skipped, "donors": donors, "serp_requests": 0}
    line = store.write_json(instance.brief_dir(tid) / "top.json", "topic_top", pages,
                            meta={k: v for k, v in report.items()}, source_count=None)
    (instance.brief_dir(tid) / "top.md").write_text(render_top(report, pages) + "\n", encoding="utf-8",
                                                    newline="\n")
    report["write"] = line
    report["pages"] = pages
    return report


def _top_stems(instance, text: str, n: int = 15) -> list[str]:
    from collections import Counter
    counts = Counter()
    for token in instance.norm.tokens(text):
        counts[max(token, key=lambda v: (len(v), v))] += 1
    return [w for w, _ in counts.most_common(n)]


def _numbers(instance, topic: dict, pages: list[dict]) -> dict:
    pr = instance.p["prepare"]
    day = instance.today.isoformat()
    sources = ", ".join(p["url"] for p in pages)
    chars = [p["chars"] for p in pages]
    raw = sum(chars) / len(chars) if pr["volume_rule"] == "mean" else max(chars)
    target, note = round(raw), f"правило {pr['volume_rule']} по {len(chars)} страницам"
    direction = _direction(instance, topic.get("direction", ""))
    if direction:
        lo, hi = direction["volume"]
        if target < lo or target > hi:
            note += f"; {target} вне коридора направления {lo}–{hi} — взята граница"
        target = min(max(target, lo), hi)
    else:
        note += "; направление темы без коридора объёма"
    images = math.ceil(max(p["images"] for p in pages) * (1 + pr["images_margin"]))
    with_table = sum(1 for p in pages if p["tables"])
    rule = pr["table_rule"]
    need_table = {"any": with_table >= 1, "majority": with_table * 2 > len(pages),
                  "all": with_table == len(pages)}[rule]

    def num(value, why):
        return {**Number(value, day, day, f"prepare top: {sources}", FACT, day, why).to_dict(),
                "sources": [p["url"] for p in pages]}
    return {"volume_chars": num(target, note),
            "images": num(images, f"наибольшее в топе {max(p['images'] for p in pages)} × "
                                  f"(1 + {pr['images_margin']}), вверх до целого"),
            "table": {**num(int(need_table), f"правило {rule}: таблица у {with_table} из {len(pages)}"),
                      "need": need_table}}


def render_top(report: dict, pages: list[dict]) -> str:
    n = report["numbers"]
    lines = [f"Разбор топа: «{report['topic']}» ({report['id']}) — {report['date']}, выдача от {report['serp_as_of']}",
             "Итог: " + ("полный" if report["complete"] else f"НЕПОЛНЫЙ — {report['incomplete_reason']}"), ""]
    for p in pages:
        lines.append(f"- {p['url']}: {p['chars']} знаков, таблиц {p['tables']}, картинок {p['images']}, списков "
                     f"{p['lists']}, FAQ {'есть' if p['faq'] else 'нет'}, разделов H2 {len(p['h2'])}")
    if n:
        lines += ["", f"Объём: {n['volume_chars']['value']} знаков ({n['volume_chars']['note']})",
                  f"Картинок: {n['images']['value']} ({n['images']['note']})",
                  f"Таблица: {'нужна' if n['table']['need'] else 'не нужна'} ({n['table']['note']})",
                  "Статус чисел: факт, источник — адреса выше, дата съёма — день разбора"]
    lines += ["", f"Пробелы ({len(report['gaps'])}) — разделы конкурентов, которых нет у нас по теме "
                  f"(наши статьи: {', '.join(report['our_pages']) or '—'}):"]
    lines += [f"  - {g['heading']} — {g['source']}" for g in report["gaps"]]
    lines += ["", f"Фактура: {report['facts']['status']} — {report['facts']['note']}"]
    for d in report["donors"]:
        lines.append(f"Донор {d['url']}: {d['status']}, тезисов {len(d['theses'])}")
    if report["skipped"]:
        lines += ["", "Не вошли:"] + [f"  - {s['url']}: {s['why']}" for s in report["skipped"]]
    if report["dropped"]:
        lines += ["Выкинуты из отбора:"] + [f"  - {d['url']}: {d['why']}" for d in report["dropped"]]
    lines.append("Запросов к источнику выдачи: 0 (снимок дня взятия)")
    return "\n".join(lines)


# --- доноры (Т18.9, решение Д5)

def _en_stems(text: str) -> set[str]:
    return {_EN.stemWord(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}


def _donor_urls(instance, tid: str) -> list[str]:
    path = instance.brief_dir(tid) / "donors.json"
    if not path.is_file():
        return []
    return [r["url"] for r in store.read_json(path, "topic_donors")["records"]]


def donors(instance, tid: str, terms: list[str], explicit: list[str]) -> dict:
    topic_of(instance, tid)
    pr = instance.p["prepare"]
    hosts = {host_unicode(d) for d in pr["donors"]}
    if not hosts:
        raise GateRefused("доноров нет: prepare.donors пуст в профиле")
    for url in explicit:
        if host_of(norm_url(url)) not in hosts:
            raise GateRefused(f"адрес донора «{url}» не с домена из prepare.donors — не загружается")
    stems = set().union(*(_en_stems(t) for t in terms)) if terms else set()
    fetcher = PageFetcher(allowed_hosts=sorted(hosts), timeout=pr["page_timeout"], max_kb=pr["page_max_kb"],
                          today=instance.today, transport=instance.transport)
    found, notes = [], []
    if stems:
        for host in sorted(hosts):
            urls, maps = [], [f"https://{host}/sitemap.xml"]
            read = 0
            while maps and read < pr["donor_sitemaps"]:
                sitemap = maps.pop(0)
                read += 1
                try:
                    got = fetcher.get(sitemap, allow_redirects=False)
                except SourceError as exc:
                    notes.append(f"{sitemap}: {exc}")
                    continue
                if got.status != 200:
                    notes.append(f"{sitemap}: HTTP {got.status}")
                    continue
                locs = LOC.findall(got.text)
                if "<sitemapindex" in got.text.lower():
                    maps += [u for u in locs if host_of(norm_url(u)) == host]
                else:
                    urls += locs
            scored = []
            for url in dict.fromkeys(urls):
                slug_stems = _en_stems(slug_of(url).replace("-", " "))
                score = len(slug_stems & stems)
                if score:
                    scored.append((-score, url))
            found += [url for _, url in sorted(scored)[:pr["donor_top"]]]
    records = [{"url": norm_url(u), "how": "явный адрес"} for u in explicit] + \
              [{"url": norm_url(u), "how": "карта сайта донора"} for u in found
               if norm_url(u) not in {norm_url(x) for x in explicit}]
    line = store.write_json(instance.brief_dir(tid) / "donors.json", "topic_donors", records,
                            meta={"id": tid, "terms": terms, "date": instance.today.isoformat(), "notes": notes})
    return {"records": records, "notes": notes, "write": line}


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("prepare", help="подготовка: top | donors | brief | check | approve")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("top", help="разбор топа в числа (выдача дня взятия, без квоты)")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("donors", help="адреса доноров: карта сайта по формулировкам на языке донора")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--terms", action="append", default=[], help="формулировка темы на языке донора")
    p.add_argument("--donor-url", action="append", default=[], help="явный адрес с домена из prepare.donors")
    p.set_defaults(func=run)
    from . import brief
    brief.register_actions(actions)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "top":
        out = top(instance, args.id)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            print(render_top(out, out["pages"]))
            print(out["write"])
        return 0 if out["complete"] else 1
    if args.action == "donors":
        if not args.terms and not args.donor_url:
            raise EngineError("нужны --terms (формулировки на языке донора) или --donor-url")
        out = donors(instance, args.id, args.terms, args.donor_url)
        for r in out["records"]:
            print(f"  {r['url']} — {r['how']}")
        for note in out["notes"]:
            print(f"  ! {note}")
        print(out["write"])
        return 0
    raise EngineError(f"неизвестное действие {args.action}")
