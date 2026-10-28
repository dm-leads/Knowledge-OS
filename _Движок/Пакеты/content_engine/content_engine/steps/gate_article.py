"""Гейт статьи без модели (В7, Т13): `content-engine gate article <файл.md> [--schema schema.json | --html страница]
[--offline]` или `gate article --topic <id>`.

Проверки и их уровень (block — черновик, warn — замечание, off — выключена) — в профиле. Итог: «пропуск» (код 0)
или «черновик» с перечнем причин (код 1); замечания итог не меняют. Ссылки проверяются HEAD, при отказе GET;
с `--offline` проверка ссылок пропускается и становится замечанием «ссылки не проверены».

Проверка `schema` сверяет разметку с текстом (Т20.1): `--schema` — JSON-LD файлом, `--html` — JSON-LD из
`<script type="application/ld+json">` и микроразметка (`itemscope`, `itemprop`) собранной страницы. `--topic <id>` —
статья машины `data/briefs/<id>/article.md` (Т19.7): поля шапки, которых нет, берутся из брифа, разметка строится
из текста и брифа и сразу проверяется; отчёт `gate_article.json` с SHA-256 текста — по нему сборка понимает, что
«пропуск» выдан этому тексту, а не прежнему.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from ..adapters.corpus import HEADING, split_front_matter
from ..core.errors import EngineError
from ..core.normalize import words
from ..core.urls import is_own, norm_url, section_of, slug_of
from .dedup import title_similarity

PASS_, DRAFT = "пропуск", "черновик"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)[^)]*\)|(?<![(\w])(https?://[^\s)<>\]]+)")
LIST_LINE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
# Служебные приписки модели, заглушки, сырая разметка, служебные даты и чек-листы — общие для любых текстов.
HYGIENE = [r"как (?:языковая )?модель", r"\bas an ai\b", r"я не могу (?:помочь|предоставить)",
           r"конечно[,!] вот", r"надеюсь, это поможет", r"lorem ipsum", r"\bTODO\b", r"\bTBD\b", r"\[ссылка\]",
           r"\[источник\]", r"\bXXX\b", r"<\/?(?:div|span|p|br)\b", r"^\s*-\s\[[ x]\]\s", r"дата сборки:",
           r"\{\{.*?\}\}"]
SHINGLE = 8


def _sections(body: str):
    """[(уровень, заголовок, текст)] по markdown-заголовкам."""
    lines = body.splitlines()
    marks = [(i, len(m.group(1)), m.group(2).strip()) for i, l in enumerate(lines) if (m := HEADING.match(l))]
    out = []
    for n, (i, level, title) in enumerate(marks):
        stop = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        out.append((level, title, "\n".join(lines[i + 1:stop])))
    return out


def _shingles(text: str) -> set[tuple]:
    w = words(text)
    return {tuple(w[i:i + SHINGLE]) for i in range(len(w) - SHINGLE + 1)}


def _link_alive(url: str, timeout: int) -> tuple[bool, str]:
    session = requests.Session()
    session.trust_env = False
    try:
        r = session.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return True, "200"
        r = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
        return r.status_code == 200, str(r.status_code)
    except requests.exceptions.RequestException as exc:
        return False, type(exc).__name__


def check_article(instance, path: Path, *, schema_path: Path | None = None, offline: bool = False,
                  link_checker=_link_alive, schema_data: dict | None = None, html_path: Path | None = None,
                  brief: dict | None = None, topic_id: str | None = None, direction: str | None = None) -> dict:
    p, g = instance.p, instance.p["gate_article"]
    norm = instance.norm
    text = Path(path).read_text(encoding="utf-8")
    meta, body = split_front_matter(text)
    value = {}
    for source, role in g["fields"].items():
        if not value.get(role):
            value[role] = str(meta.get(source) or "").strip()
    for role in ("title", "description", "slug"):           # статья машины: чего нет в шапке — из брифа
        if brief and not value.get(role):
            value[role] = str(brief.get(role) or "").strip()
    schema = schema_data
    if schema_path:
        try:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            schema = exc
    if not value.get("slug") and isinstance(schema, dict):
        value["slug"] = str(schema.get("slug") or "")
    sections = _sections(body)
    h1 = [t for level, t, _ in sections if level == 1]
    h2 = [t for level, t, _ in sections if level == 2]
    title = value.get("title") or (h1[0] if h1 else "")
    slug = value.get("slug", "")
    results = []

    def add(name, ok, detail):
        level = g["levels"][name]
        if level != "off":
            results.append({"check": name, "level": level, "ok": bool(ok), "detail": detail})

    lo, hi = g["title_length"]
    add("title_length", lo <= len(title) <= hi, f"title {len(title)} знаков, норма {lo}–{hi}")
    desc = value.get("description", "")
    lo, hi = g["description_length"]
    add("description_length", lo <= len(desc) <= hi, f"description {len(desc)} знаков, норма {lo}–{hi}")
    add("slug", bool(SLUG.match(slug)), f"slug «{slug}»: латиница нижнего регистра, цифры, одиночные дефисы")
    # дубль заголовка и скелет — против живых статей, кроме самой статьи
    index = instance.index()
    live = [item for item in index.pages if item.page.slug != slug]
    tkey = norm.key(title)
    best = max(((title_similarity(tkey, item.title_key), item.page.title) for item in live), default=(0.0, ""))
    t = p["thresholds"]["title_overlap"]
    add("title_duplicate", best[0] < t, f"сходство заголовка с «{best[1]}» {best[0]:.2f} (порог {t})")
    faq = [(lvl, ttl, txt) for lvl, ttl, txt in sections
           if lvl >= 2 and ttl.lower().startswith(g["faq_heading"].lower())]
    missing = []
    if len(h1) != 1:
        missing.append(f"H1: {len(h1)} вместо одного")
    if len(h2) < 2:
        missing.append("разделов H2 меньше двух")
    if not faq:
        missing.append(f"нет блока FAQ («{g['faq_heading']}…»)")
    if g["cta_markers"]:
        markers = [m.lower() for m in g["cta_markers"]]
        in_body = any(m in line.lower() for line in body.splitlines() if not HEADING.match(line) for m in markers)
        in_heading = any(m in ttl.lower() for _, ttl, _ in sections for m in markers)
        if not in_body:
            missing.append("нет призыва к действию")
        if in_heading:
            missing.append("призыв к действию оформлен заголовком, а не блоком")
    add("structure", not missing, "; ".join(missing) or "H1, разделы, FAQ и призыв к действию на месте")
    if h2:
        skel = [norm.key(x) for x in h2]
        worst = (0.0, "")
        for item in live:
            heads = [norm.key(s.heading) for s in item.page.sections if s.heading]
            if not heads:
                continue
            share = sum(1 for k in skel if any(title_similarity(k, h) >= t for h in heads)) / len(skel)
            if share > worst[0]:
                worst = (share, item.page.title)
        add("skeleton", worst[0] < 0.6, f"доля разделов, повторяющих скелет «{worst[1]}»: {worst[0]:.2f}")
    # чужие целевые ключи — по карте спроса
    data_path = instance.file("demand_map")
    if data_path.is_file():
        from ..core import store
        pages = store.read_json(data_path, "demand_map")["records"]
        from ..core.foreign import foreign_hits
        heads = [title] + h1 + h2
        head_keys = [(h, norm.key(h)) for h in heads]
        links = {norm_url(u) for u in _links(body)}
        sentences = [(s, norm.key(s)) for s in re.split(r"(?<=[.!?])\s+", body)]
        hits_taken, hits_mentioned = foreign_hits(pages, head_keys, sentences, links, norm, t, slug)
        taken = [f"заголовок «{h.phrase}» таргетирует «{h.target}» страницы {h.page['url']}" for h in hits_taken]
        mentioned = [f"«{h.target}» упомянут без ссылки на {h.page['url']}" for h in hits_mentioned]
        add("foreign_keys", not taken, "; ".join(taken[:5]) or "чужие целевые ключи заголовками не взяты")
        add("foreign_mentions", not mentioned, "; ".join(mentioned[:5]) or "чужие ключи упомянуты со ссылкой")
    else:
        results.append({"check": "foreign_keys", "level": "warn", "ok": False,
                        "detail": "карты спроса нет — чужие ключи не проверены (`map build`)"})
    mine = _shingles(body)
    worst = (0.0, "")
    for item in live:
        theirs = _shingles(item.page.text)
        if mine and theirs:
            share = len(mine & theirs) / len(mine)
            if share > worst[0]:
                worst = (share, item.page.title)
    tw = p["thresholds"]["twin_shingles"]
    add("twin", worst[0] < tw, f"общих восьмисловных цепочек с «{worst[1]}»: {worst[0]:.3%} (порог {tw:.1%})")
    questions = 0
    for _, _, txt in faq:
        questions += sum(1 for line in txt.splitlines() if line.strip().strip("*_ ").endswith("?"))
    questions += sum(1 for lvl, ttl, _ in sections if lvl >= 3 and ttl.endswith("?"))
    lo, hi = g["faq_questions"]
    add("faq", lo <= questions <= hi, f"вопросов в FAQ: {questions}, норма {lo}–{hi}")
    domains = p["site"]["domains"]
    all_links = [f"https://{domains[0]}{u}" if u.startswith("/") else u for u in _links(body)]
    sales_urls = {norm_url(u) for u in p["site"]["sales_urls"]}
    sales = [u for u in all_links if is_own(u, domains) and (section_of(u) in p["site"]["sales_sections"]
                                                             or norm_url(u) in sales_urls)]
    lo, hi = g["sales_links"]
    add("sales_links", lo <= len(sales) <= hi, f"ссылок на продающие страницы: {len(sales)}, норма {lo}–{hi}")
    external = {norm_url(u).split("/")[0] for u in all_links if u.startswith("http") and not is_own(u, domains)}
    add("sources", len(external) >= g["min_sources"], f"внешних источников (доменов): {len(external)}, "
                                                       f"нужно не меньше {g['min_sources']}")
    if offline:
        if g["levels"]["links_alive"] != "off":
            results.append({"check": "links_alive", "level": "warn", "ok": False, "detail": "ссылки не проверены"})
    else:
        dead = []
        for url in dict.fromkeys(u for u in all_links if u.startswith("http")):
            ok, why = link_checker(url, g["link_timeout"])
            if not ok:
                dead.append(f"{url} — {why}")
        add("links_alive", not dead, "; ".join(dead[:5]) or f"ссылок проверено: {len(set(all_links))}")
    plain = [l for l in body.splitlines() if l.strip() and not HEADING.match(l)]
    total = sum(len(l) for l in plain) or 1
    share = sum(len(l) for l in plain if LIST_LINE.match(l)) / total
    add("list_share", share <= g["max_list_share"], f"доля текста в списках {share:.0%}, не больше "
                                                     f"{g['max_list_share']:.0%}")
    patterns = HYGIENE + g["hygiene_patterns"]
    dirt = [m.group(0) for rx in patterns for m in [re.search(rx, body, re.IGNORECASE | re.MULTILINE)] if m]
    add("hygiene", not dirt, "найдено: " + "; ".join(f"«{d.strip()}»" for d in dirt[:5]) if dirt else "чисто")
    low = body.lower()
    stop = [s for s in g["stop_phrases"] if s.lower() in low]
    add("stop_phrases", not stop, "найдено: " + ", ".join(stop) if stop else "чисто")
    if schema_path or schema_data is not None or html_path:
        from . import schema as schema_mod
        faq_text = " ".join(txt for _, _, txt in faq) + " " + " ".join(t2 for _, t2, _ in sections)
        problems, kind = [], "JSON-LD"
        if html_path:
            from ..core.htmldoc import HtmlDoc
            doc = HtmlDoc(Path(html_path).read_text(encoding="utf-8"))
            problems += [f"JSON-LD на странице не читается — {b}" for b in doc.json_ld() if isinstance(b, Exception)]
            nodes = doc.schema_nodes()
            kind = "JSON-LD и микроразметка страницы" if doc.microdata() else "JSON-LD страницы"
            schema_slug = slug_of(doc.canonical) if doc.canonical else ""
        elif isinstance(schema, Exception):
            problems.append(f"JSON не читается — {schema}")
            nodes, schema_slug = None, ""
        else:
            nodes = schema.get("@graph", [schema]) if isinstance(schema, dict) else []
            schema_slug = str(schema.get("slug") or "") if isinstance(schema, dict) else ""
        if nodes is not None:
            problems += schema_mod.problems(nodes, schema_slug=schema_slug, title=title, h1s=h1, slug=slug,
                                            faq_text=faq_text)
        add("schema", not problems, "; ".join(problems) or f"разметка ({kind}) совпадает с текстом")
    if "rhythm" in g["levels"]:                 # часть 2 (профиль версии 3): текст, вычитка, ниша, картинки
        import hashlib
        from . import text_checks
        text_checks.run(instance, add, results, body=body, sections=sections, faq=faq, brief=brief,
                        topic_id=topic_id, direction=direction, links=_links(body),
                        article_sha=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    if "refresh_change" in g["levels"] and topic_id:   # профиль версии 5: правка страницы против эталона
        _refresh_change(instance, add, topic_id, body)
    reasons = [r for r in results if r["level"] == "block" and not r["ok"]]
    notes = [r for r in results if r["level"] == "warn" and not r["ok"]]
    return {"file": str(path), "verdict": DRAFT if reasons else PASS_, "reasons": reasons, "notes": notes,
            "checks": results, "offline": offline}


def _refresh_change(instance, add, topic_id: str, body: str):
    """Т36.4: у правки страницы доля новых восьмисловных цепочек против эталона дня взятия не ниже порога."""
    from . import refresh
    try:
        from .queue import _find, load
        topic = _find(load(instance), topic_id)
    except EngineError:
        return
    if not refresh.is_refresh(topic):
        return
    base = refresh.baseline_of(instance, topic)
    share = refresh.change_share(base["text"], body)
    floor = instance.p["refresh"]["min_change"]
    add("refresh_change", share >= floor,
        f"новых восьмисловных цепочек {share:.0%} против эталона {base['day']} (порог {floor:.0%})"
        + ("" if share >= floor else " — пустое обновление: новая дата без изменения по существу не публикуется"))


def _links(body: str) -> list[str]:
    out = []
    for m in LINK.finditer(body):
        out.append((m.group(1) or m.group(2)).rstrip(".,;"))
    return out


def render(report: dict) -> str:
    lines = [f"Гейт статьи: {report['file']}", f"Итог: {report['verdict'].upper()}"]
    for r in report["reasons"]:
        lines.append(f"  ✗ {r['check']}: {r['detail']}")
    for r in report["notes"]:
        lines.append(f"  ! {r['check']} (замечание): {r['detail']}")
    lines.append(f"проверок: {len(report['checks'])}, причин: {len(report['reasons'])}, "
                 f"замечаний: {len(report['notes'])}")
    return "\n".join(lines)


def register(sub):
    pass                                   # подкоманда `gate` регистрируется в gate_topic, здесь — её вид article


def register_kind(kinds):
    from ..cli import add_profile
    p = kinds.add_parser("article", help="гейт готовой статьи без модели")
    add_profile(p)
    p.add_argument("file", nargs="?", help="файл статьи (не нужен с --topic)")
    p.add_argument("--topic", help="id темы: статья машины data/briefs/<id>/article.md, разметка из текста и брифа")
    p.add_argument("--schema", help="schema.json статьи")
    p.add_argument("--html", help="собранная страница: разметка JSON-LD и микроразметка из HTML")
    p.add_argument("--offline", action="store_true", help="не проверять ссылки")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.topic:
        report = check_topic(instance, args.topic, offline=args.offline)
    else:
        if not args.file or not Path(args.file).is_file():
            raise EngineError(f"файл статьи не найден: {args.file}")
        report = check_article(instance, Path(args.file), schema_path=Path(args.schema) if args.schema else None,
                               html_path=Path(args.html) if args.html else None, offline=args.offline)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else render(report))
    return 0 if report["verdict"] == PASS_ else 1


def check_topic(instance, tid: str, *, offline: bool = False, link_checker=_link_alive) -> dict:
    """Т19.7: статья машины файлом → разметка из текста и брифа → гейт → отчёт с суммой текста."""
    import hashlib
    from . import schema as schema_mod
    from .brief import load_brief
    folder = instance.brief_dir(tid)
    article = folder / "article.md"
    if not article.is_file():
        raise EngineError(f"тема {tid}: текста нет — положите его файлом {article} (маршрут проекта, Т19.7)")
    brief = load_brief(instance, tid)
    text = article.read_text(encoding="utf-8")
    data = schema_mod.build(text, brief, instance.p, instance.today.isoformat())
    (folder / "schema.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8",
                                        newline="\n")
    try:
        from .queue import _find, load
        direction = _find(load(instance), tid).get("direction")
    except Exception:                                   # тема не из очереди — объём не меряется
        direction = None
    report = check_article(instance, article, schema_data=data, brief=brief, offline=offline,
                           link_checker=link_checker, topic_id=tid, direction=direction)
    report.update(id=tid, date=instance.today.isoformat(),
                  article_sha256=hashlib.sha256(article.read_bytes()).hexdigest())
    (folder / "gate_article.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8",
                                              newline="\n")
    return report
