"""Аудит недели (модуль Д3, Т35): `content-engine audit run | checklist | accept`.

Техника — по всей карте сайта по техническому чек-листу проекта (`tech_checklist.yaml`, Т35.1–Т35.3): title и
description, H1, canonical (нет, чужой домен, битая цель, цель закрыта, цепочка), ответы 4xx и 5xx, перенаправления,
тип разметки статьи, карта сайта и robots, экспертность баллом, сироты по графу внутренних ссылок, чистота текста,
директивы эксперта. Четыре уровня важности. Свой сайт обходится с паузой `audit.delay_seconds`, только GET, белый
список — свои домены. Итог — снимок `data/audit/<дата>.json` (`core/audit_store.py`), граф `data/link_graph.json` и
отчёт `reports/<дата>/audit.md` — **только дельта** к прошлому снимку (Т35.6); первый прогон — «база снята».
Пересечения живых, выпавшие из поиска и советы — `overlaps.py` рядом (Т35.4–Т35.6).
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from importlib import resources
from pathlib import Path

import yaml

from ..core import audit_store
from ..core.errors import EngineError, NetworkUnreachable, SourceError, source_failures
from ..core.htmldoc import HtmlDoc
from ..core.urls import host_of, is_own, norm_url, section_of, slug_of

SLEEP = time.sleep
UNCHECKED = "не проверено: "
CHECKLIST_VERSION = 1
REDIRECTS = (301, 302, 303, 307, 308)


def checklist_template() -> str:
    return resources.files("content_engine.templates").joinpath("tech_checklist.yaml").read_text(encoding="utf-8")


def load_checklist(instance) -> dict | None:
    path = Path(instance.p["audit"]["checklist"])
    path = path if path.is_absolute() else instance.root / path
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("schema_version") != CHECKLIST_VERSION:
        raise EngineError(f"{path.name}: schema_version {data.get('schema_version')} — пакет понимает {CHECKLIST_VERSION}")
    checks = data.get("checks") or {}
    for name, spec in checks.items():
        if spec.get("level") not in audit_store.LEVELS + ("off",):
            raise EngineError(f"{path.name}: checks.{name}.level — одно из {audit_store.LEVELS + ('off',)}")
    return checks


class Crawl:
    """Обход своих страниц: GET без перенаправлений, цепочка — вручную; пауза между загрузками."""

    def __init__(self, instance, transport=None):
        from ..adapters.page_fetch import PageFetcher
        self.instance = instance
        self.fetcher = PageFetcher.for_own_site(instance, transport=transport if transport is not None
                                                else instance.transport)
        self.delay = instance.p["audit"]["delay_seconds"]
        self.pages: dict[str, dict] = {}
        self.loads = 0

    def get(self, url: str) -> dict:
        key = norm_url(url)
        if key in self.pages:
            return self.pages[key]
        if self.loads and self.delay:
            SLEEP(self.delay)
        self.loads += 1
        try:
            page = self.fetcher.get(url if url.startswith("http") else "https://" + url, allow_redirects=False)
        except NetworkUnreachable as exc:
            got = {"url": key, "status": None, "error": str(exc), "network": True}
        except SourceError as exc:
            got = {"url": key, "status": None, "error": str(exc)}
        else:
            got = {"url": key, "status": page.status, "location": page.location, "text": page.text,
                   "robots_header": page.robots_header}
        self.pages[key] = got
        return got


def _find(findings, check, url, level, detail, first_seen):
    fid = audit_store.finding_id(check, url)
    findings.append({"id": fid, "url": url, "check": check, "level": level, "detail": detail,
                     "first_seen": first_seen.get(fid, "")})


def _expertise(doc: HtmlDoc, markers: dict, url: str, spec: dict) -> tuple[int, list[str]]:
    w = spec.get("weights") or {}
    nodes = doc.schema_nodes()
    main, _ = doc.main()
    links = [norm_url(a.attrs.get("href", "")) for a in main.find_all("a") if a.attrs.get("href")]
    own = [l for l in links if l and host_of(l) == host_of(url)]
    foreign = [l for l in links if l and host_of(l) and host_of(l) != host_of(url)]
    has = {
        "author": any(n.get("author") for n in nodes) or bool(doc._meta("author"))
        or any("author" in (x.attrs.get("class", "") + x.attrs.get("itemprop", "")) for x in doc.root.iter()),
        "date": any(n.get("datePublished") or n.get("dateModified") for n in nodes) or bool(doc.root.find_all("time")),
        "internal_links": len(set(own)) >= 2,
        "sources": len(set(foreign)) >= 1,
        "volume": markers["chars"] >= spec.get("min_chars", 3000),
        "faq": markers["faq"],
    }
    score = sum(w.get(k, 1) for k, ok in has.items() if ok)
    return score, [k for k, ok in has.items() if not ok]


def run_audit(instance, *, transport=None) -> dict:
    from ..adapters.corpus import sitemap_urls
    checks = load_checklist(instance)
    if checks is None:
        raise EngineError(f"технического чек-листа нет ({instance.p['audit']['checklist']}): «не подключено» — "
                          "`content-engine audit checklist --apply`")
    level = {k: v.get("level") for k, v in checks.items()}
    on = {k for k, v in level.items() if v != "off"}
    prev = audit_store.latest(instance, before=instance.today.isoformat())
    first_seen = {f["id"]: f["first_seen"] or prev["date"] for f in (prev or {}).get("findings", [])}
    today = instance.today.isoformat()
    findings: list[dict] = []

    def add(check, url, detail):
        if check in on:
            _find(findings, check, url, level[check], detail, first_seen)

    domain = instance.p["site"]["domains"][0]
    crawl = Crawl(instance, transport)
    try:
        urls = sitemap_urls(instance.p, instance.root, transport=transport if transport is not None
                            else instance.transport)
    except source_failures() as exc:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout, NetworkUnreachable)):
            # сеть устройства не отвечает — это не находка сайта: аудит не проводится, снимок не пишется
            raise NetworkUnreachable(f"карта сайта: сеть не отвечает — аудит не проведён, повторите позже ({exc})")                 from None
        urls = []
        add("sitemap", domain, f"карта сайта не читается: {exc}")
    if not urls and not any(f["check"] == "sitemap" for f in findings):
        add("sitemap", domain, "карта сайта пуста или не задана (site.sitemap_url / sitemap_file)")
    robots = crawl.get(f"https://{domain}/robots.txt")
    if robots["status"] != 200:
        add("robots", domain, f"robots.txt: {robots.get('error') or 'HTTP ' + str(robots['status'])}")
    elif re.search(r"(?ims)^user-agent:\s*\*\s*$(?:(?!^user-agent:).)*?^disallow:\s*/\s*$", robots["text"]):
        add("robots", domain, "robots.txt закрывает весь сайт для всех роботов")
    old_by_url = defaultdict(list)
    for f in (prev or {}).get("findings", []):
        old_by_url[f["url"]].append(f)
    titles, descriptions = defaultdict(list), defaultdict(list)
    graph: dict[str, list[str]] = {}
    unreachable = 0
    article_sections = instance.p["site"]["article_sections"]
    p = instance.p
    cta = [w.lower() for w in p["gate_article"]["cta_markers"]]
    patterns = [re.compile(x, re.I) for x in p["gate_article"]["ai_tone_patterns"]] + \
               [re.compile(re.escape(x), re.I) for x in p["gate_article"]["stop_phrases"]]
    own_sites = {h.lower() for h in (checks.get("expert_own_sites") or {}).get("own_sites", [])}
    for url in urls:
        got = crawl.get(url)
        if got["status"] is None:
            unreachable += 1
            if not got.get("network"):
                add("http_error", url, got["error"])
            else:                              # сеть не ответила — прошлые находки страницы не «ушли»
                findings += carry(old_by_url.get(norm_url(url), []))
            continue
        if got["status"] in REDIRECTS:
            add("redirect_in_sitemap", url, f"HTTP {got['status']} → {got['location']}")
            hops, target = 1, got["location"]
            while target and hops <= 5:
                nxt = crawl.get(target) if is_own(target, p["site"]["domains"]) else None
                if not nxt or nxt["status"] not in REDIRECTS:
                    break
                hops, target = hops + 1, nxt["location"]
            if hops > (checks.get("redirect_chain") or {}).get("max", 1):
                add("redirect_chain", url, f"перенаправлений подряд: {hops}")
            continue
        if got["status"] >= 400:
            add("http_error", url, f"HTTP {got['status']}")
            continue
        doc = HtmlDoc(got["text"], p["prepare"]["main_blocks"])
        markers = doc.markers()
        if "noindex" in f"{doc.robots} {got.get('robots_header', '')}".lower():
            add("noindex_in_sitemap", url, "в карте сайта, но закрыта от индексации")
        title, desc = doc.title.strip(), doc.description.strip()
        if not title:
            add("title_empty", url, "title пуст")
        else:
            titles[title].append(url)
            lo, hi = (checks.get("title_length") or {}).get("range", [30, 70])
            if not lo <= len(title) <= hi:
                add("title_length", url, f"title {len(title)} знаков (норма {lo}–{hi})")
        if not desc:
            add("description_empty", url, "description пуст")
        else:
            descriptions[desc].append(url)
            lo, hi = (checks.get("description_length") or {}).get("range", [70, 170])
            if not lo <= len(desc) <= hi:
                add("description_length", url, f"description {len(desc)} знаков (норма {lo}–{hi})")
        if not doc.headings(1):
            add("h1_missing", url, "H1 нет")
        canon = doc.canonical
        if not canon:
            add("canonical_missing", url, "canonical нет")
        elif norm_url(canon) != norm_url(url):
            if not is_own(canon, p["site"]["domains"]):
                add("canonical_foreign", url, f"canonical на чужой домен: {canon}")
            else:
                target = crawl.get(canon)
                if target["status"] != 200:
                    add("canonical_broken", url, f"цель canonical {canon}: "
                                                 f"{target.get('error') or 'HTTP ' + str(target['status'])}")
                else:
                    tdoc = HtmlDoc(target["text"], p["prepare"]["main_blocks"])
                    if "noindex" in tdoc.robots.lower():
                        add("canonical_noindex", url, f"цель canonical {canon} закрыта от индексации")
                    if tdoc.canonical and norm_url(tdoc.canonical) not in (norm_url(canon), norm_url(url)):
                        add("canonical_chain", url, f"{url} → {canon} → {tdoc.canonical}")
        is_article = section_of(url) in article_sections
        main, _ = doc.main()
        links = [norm_url(a.attrs.get("href", "")) for a in main.find_all("a") if a.attrs.get("href")]
        graph[norm_url(url)] = sorted({l for l in links if l and is_own(l, p["site"]["domains"])
                                       and l != norm_url(url)})
        raw_links = [a.attrs.get("href", "") for a in main.find_all("a") if a.attrs.get("href")]
        if any("?" in h and is_own(norm_url(h), p["site"]["domains"]) for h in raw_links):
            add("expert_link_params", url, "внутренние ссылки с параметрами")
        if own_sites and any(host_of(l) in own_sites for l in links if l):
            add("expert_own_sites", url, "ссылка на другой свой сайт")
        if not is_article:
            continue
        types = {str(n.get("@type")) for n in doc.schema_nodes()}
        want = (checks.get("schema_type") or {}).get("article", ["Article"])
        if not types & set(want):
            add("schema_type", url, f"разметка статьи: нет {want}; есть {sorted(types) or 'ничего'}")
        if cta and any(any(w in h.lower() for w in cta) for h in markers["h2"] + markers["h3"]):
            add("expert_cta_heading", url, "призыв к действию — заголовком")
        has_faq_heading = any(re.search(r"вопрос|faq", h, re.I) for h in markers["h2"] + markers["h3"])
        if has_faq_heading and "FAQPage" not in types:
            add("expert_faq_schema", url, "раздел вопросов без разметки FAQPage")
        if not re.fullmatch(r"[a-z0-9-]+", slug_of(url) or ""):
            add("expert_slug", url, f"слаг «{slug_of(url)}» — не латиница в нижнем регистре")
        spec = checks.get("expertise") or {}
        score, missing = _expertise(doc, markers, url, spec)
        if score < spec.get("min_score", 4):
            add("expertise", url, f"балл {score}; дописать: {', '.join(missing)}")
        hits = sum(len(rx.findall(markers["text"])) for rx in patterns)
        if hits:
            add("text_cleanliness", url, f"штампов и оценочных формулировок: {hits}")
    for value, pages in list(titles.items()):
        if len(pages) > 1:
            for u in pages:
                add("title_duplicate", u, f"title «{value}» ещё у {len(pages) - 1} стр.")
    for value, pages in list(descriptions.items()):
        if len(pages) > 1:
            for u in pages:
                add("description_duplicate", u, f"description повторяется у {len(pages)} стр.")
    incoming = Counter(t for src, targets in graph.items() for t in targets)
    for u in graph:
        if section_of(u) in article_sections and not incoming.get(u):
            add("orphan", u, "нет входящих внутренних ссылок со страниц сайта")
    for f in findings:
        f["first_seen"] = f["first_seen"] or today
    graph_path = instance.root / "data" / "link_graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({"date": today, "graph": graph}, ensure_ascii=False), encoding="utf-8",
                          newline="\n")
    return {"pages": len(urls), "loads": crawl.loads, "unreachable": unreachable, "findings": findings,
            "previous": prev, "graph": graph, "crawl": crawl}


def carry(old: list[dict]) -> list[dict]:
    """Находки прошлого снимка по адресам, которые в этот раз не проверены: остаются с пометкой, не «ушли»."""
    return [{**f, "detail": f["detail"] if f["detail"].startswith(UNCHECKED) else UNCHECKED + f["detail"]}
            for f in old]


def delta(prev: dict | None, findings: list[dict]) -> dict:
    if prev is None:
        return {"base": True, "new": findings, "gone": [], "changed": []}
    old = {f["id"]: f for f in prev["findings"]}
    new = {f["id"]: f for f in findings}
    return {"base": False, "new": [f for k, f in new.items() if k not in old],
            "gone": [f for k, f in old.items() if k not in new],
            "changed": [(old[k], f) for k, f in new.items() if k in old and old[k]["level"] != f["level"]]}


def render(out: dict, d: dict, extra: list[str]) -> str:
    counts = Counter(f["level"] for f in out["findings"])
    lines = [f"# Аудит недели — страниц {out['pages']}, загрузок {out['loads']}"
             + (f", не ответили (сеть) {out['unreachable']}" if out["unreachable"] else ""),
             "Находок: " + ", ".join(f"{lvl} — {counts.get(lvl, 0)}" for lvl in audit_store.LEVELS), ""]
    if d["base"]:
        lines.append("Первый прогон — база снята; дальше в отчёте только дельта.")
    lines.append(f"## Новые ({len(d['new'])})")
    lines += [f"- {f['level']}: {f['check']} — {f['url']}: {f['detail']}"
              for f in sorted(d["new"], key=lambda f: audit_store.LEVELS.index(f["level"]))]
    lines.append(f"## Ушли ({len(d['gone'])})")
    lines += [f"- {f['check']} — {f['url']}" for f in d["gone"]]
    if d["changed"]:
        lines.append(f"## Сменили уровень ({len(d['changed'])})")
        lines += [f"- {a['check']} — {a['url']}: {a['level']} → {b['level']}" for a, b in d["changed"]]
    return "\n".join(lines + extra) + "\n"


def write_checklist(instance, apply: bool) -> str:
    path = Path(instance.p["audit"]["checklist"])
    path = path if path.is_absolute() else instance.root / path
    if path.is_file():
        return f"{path.name} уже есть — не перезаписан"
    if not apply:
        return f"план: положить шаблон технического чек-листа в {path} — выполнить с --apply"
    path.write_text(checklist_template(), encoding="utf-8", newline="\n")
    if load_checklist(instance) is None:
        raise EngineError(f"{path.name}: не прочитался обратно")
    return f"{path.name}: записан шаблон — правьте уровни и параметры под проект"


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("audit", help="аудит недели: run | checklist | accept")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("run", help="аудит по техническому чек-листу; в отчёте — дельта к прошлому снимку")
    add_profile(p)
    p.add_argument("--full", action="store_true", help="пересечения живых — по всем статьям, а не изменившимся")
    p.add_argument("--unattended", action="store_true", help="запуск без человека (расписание); платных вызовов нет")
    p.set_defaults(func=run)
    p = actions.add_parser("checklist", help="положить шаблон технического чек-листа в инстанс")
    add_profile(p)
    p.add_argument("--apply", action="store_true", help="записать файл (только в инстанс)")
    p.set_defaults(func=run)
    p = actions.add_parser("advice", help="проверить ответ агента на задание советов (advice.json)")
    add_profile(p)
    p.add_argument("file", help="путь к advice.json")
    p.set_defaults(func=run)
    p = actions.add_parser("accept", help="«утверждаю» правку выпавшей страницы или склейку пересечения")
    add_profile(p)
    p.add_argument("id", help="id находки из отчёта аудита")
    p.add_argument("--who", required=True, help="имя человека")
    p.add_argument("--reject", action="store_true", help="не утверждаю")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "checklist":
        print(write_checklist(instance, args.apply))
        return 0
    from . import overlaps
    if args.action == "accept":
        print(overlaps.accept(instance, args.id, args.who, reject=args.reject))
        return 0
    if args.action == "advice":
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        problems = overlaps.check_advice(instance, data)
        print("\n".join(problems) or f"советы приняты: {len(data.get('advice') or [])}")
        return 1 if problems else 0
    out = run_audit(instance)
    extra = overlaps.run_all(instance, out, full=args.full)
    findings = out["findings"] + extra["findings"]
    d = delta(out["previous"], findings)
    line = audit_store.write(instance, "full" if args.full else "changed", out["pages"], findings)
    text = render({**out, "findings": findings}, d, extra["lines"])
    (instance.reports_dir() / "audit.md").write_text(text, encoding="utf-8", newline="\n")
    print(text + line)
    return 0
