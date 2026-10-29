"""Бриф и его утверждение (В2, Т19): `content-engine prepare brief | check | approve <id>`.

- `brief <id>` — задание роли `planner` (`task_brief.md`): тема, формулировки, кластер и его запросы, разбор топа,
  кандидаты в авторы (по направлению, сверх предела в месяц — предупреждение и следующий), кандидаты перелинковки
  (верхние статьи корпуса по весу совпадения основ темы **по телам** и продающие страницы кластера), мост и схема
  брифа. Кандидаты сохраняются в `brief_context.json` — по ним `check` сверяет ссылки брифа.
- `check <id>` — бриф проверяется кодом (Т19.2–Т19.5): длины title и description, slug, чужие целевые ключи в H1 и
  H2, почти-дубль заголовка среди живых, FAQ (число, основы своего запроса кластера, вопросительное слово, «?»),
  автор, ссылки вне кандидатов (предупреждение), мост и граница честности, место призыва к действию. Ошибки —
  списком, код 1.
- `approve <id> --who <имя>` — только после чистого `check`; утверждение снимается правкой брифа (сумма SHA-256).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from importlib import resources

import yaml

from ..core import approval, store, tasks
from ..core.errors import EngineError, GateRefused
from ..core.urls import norm_url, slug_of
from .dedup import BODY_CAP, concepts_of, title_similarity
from .gate_article import SLUG

# Начала вопросительных слов: «как» — какой, каком, какую; «сколь» — сколько; «нужн» — нужно, нужен
QUESTION_STARTS = ("как", "что", "чем", "чём", "чег", "чему", "почему", "зачем", "отчего", "когда", "где", "куд",
                   "откуд", "сколь", "кто", "кого", "кому", "можно", "нужн", "надо", "стоит", "чей", "чья", "чьи",
                   "чьё", "наскольк")
PREPOSITIONS = {"в", "во", "на", "для", "из", "с", "со", "при", "по", "о", "об", "до", "от", "за", "к", "у"}
LEVELS = ("обычный", "усиленный")
CONTEXT = "brief_context.json"


def schema_text() -> str:
    return resources.files("content_engine.templates").joinpath("brief.schema.yaml").read_text(encoding="utf-8")


def load_brief(instance, tid: str) -> dict:
    path = instance.brief_dir(tid) / approval.BRIEF
    if not path.is_file():
        raise GateRefused(f"тема {tid}: брифа нет — агент заполняет его по `task_brief.md` (`prepare brief {tid}`)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EngineError(f"brief.yaml темы {tid}: YAML не читается — {exc}") from exc
    if not isinstance(data, dict):
        raise EngineError(f"brief.yaml темы {tid}: верхний уровень — не раздел")
    return data


def cluster_queries(instance, topic: dict) -> list[str]:
    from . import cluster as cluster_mod
    if not topic.get("cluster_id") or not instance.file("clusters").is_file():
        return list(topic["formulations"])
    for c in cluster_mod.load(instance):
        if c["id"] == topic["cluster_id"]:
            return [q["query"] for q in c["queries"]]
    return list(topic["formulations"])


def _month_load(instance) -> Counter:
    """Сколько утверждённых брифов у каждого автора в текущем месяце."""
    month = instance.today.isoformat()[:7]
    counts = Counter()
    base = instance.topics_root()
    for mark in base.glob(f"*/{approval.APPROVAL}"):
        record = json.loads(mark.read_text(encoding="utf-8"))
        if not str(record.get("at", "")).startswith(month):
            continue
        brief = mark.parent / approval.BRIEF
        if brief.is_file():
            author = (yaml.safe_load(brief.read_text(encoding="utf-8")) or {}).get("author")
            if author:
                counts[str(author)] += 1
    return counts


def author_candidates(instance, direction: str) -> tuple[list[dict], list[str]]:
    load = _month_load(instance)
    chosen, notes = [], []
    for a in instance.p["brief"]["authors"]:
        if direction not in a["directions"]:
            continue
        used = load.get(a["name"], 0)
        if used >= a["month_limit"]:
            notes.append(f"автор {a['name']}: в этом месяце {used} статей при пределе {a['month_limit']} — "
                         "следующий по списку")
            continue
        chosen.append({**a, "used_this_month": used})
    if not chosen:
        notes.append(f"для направления «{direction}» нет автора в пределе месяца — brief.authors в профиле")
    return chosen, notes


def link_candidates(instance, topic: dict, queries: list[str]) -> list[dict]:
    """Т19.4: статьи корпуса по весу совпадения основ темы в телах (не в заголовках) и продающие страницы кластера."""
    index = instance.index()
    concepts = concepts_of([instance.norm.key(f) for f in topic["formulations"]])
    scored = []
    for item in index.pages:
        counts = [index.count(item.counts, part) for part in concepts]
        weight = sum(min(c, BODY_CAP) for c in counts)
        if weight:
            scored.append({"url": item.page.url, "slug": item.page.slug, "title": item.page.title,
                           "weight": weight, "kind": "статья", "matched": sum(1 for c in counts if c)})
    scored.sort(key=lambda x: (-x["matched"], -x["weight"], x["slug"]))
    out = scored[:instance.p["brief"]["link_candidates"]]
    path = instance.file("demand_map")
    if path.is_file():
        wanted = set(queries)
        for page in store.read_json(path, "demand_map")["records"]:
            if page["type"] == "sales" and wanted & set(page["target_keys"] + page["head_queries"]):
                out.append({"url": page["url"], "slug": page["id"], "title": page.get("title", ""), "weight": 0,
                            "kind": "продающая страница кластера", "matched": 0})
    return out


def make_task(instance, tid: str) -> dict:
    from .prepare import topic_of
    topic = topic_of(instance, tid)
    top_path = instance.brief_dir(tid) / "top.json"
    if not top_path.is_file():
        raise GateRefused(f"тема {tid}: разбора топа нет — `prepare top {tid}`")
    top = store.read_json(top_path, "topic_top")
    queries = cluster_queries(instance, topic)
    authors, author_notes = author_candidates(instance, topic.get("direction", ""))
    links = link_candidates(instance, topic, queries)
    g = instance.p["gate_article"]
    from ..core.bets import NO_BET, bet_missing
    if bet_missing(topic):
        author_notes.append(NO_BET.replace("<id>", tid))
    context = {"id": tid, "date": instance.today.isoformat(), "queries": queries, "authors": authors,
               "author_notes": author_notes, "links": links}
    (instance.brief_dir(tid) / CONTEXT).write_text(json.dumps(context, ensure_ascii=False, indent=1),
                                                   encoding="utf-8", newline="\n")
    n = top.get("numbers") or {}
    refresh_lines = []
    if topic.get("type") == "refresh":
        from . import refresh
        base = refresh.baseline_of(instance, topic)
        gaps = (top.get("refresh") or {}).get("gaps", [])
        refresh_lines = [
            f"## Правка живой страницы {topic['page']}", "",
            f"- slug — `{topic['slug']}`, как у страницы: правится та же запись, новой страницы нет;",
            f"- эталон дня взятия ({base['day']}): `{topic['baseline']['path']}` — разделы: "
            + "; ".join(base["markers"]["h2"][:15]) + f"; {base['markers']['chars']} знаков;",
            "- чего не хватает против топ-3:", *([f"  - {x['kind']}: {x['what']}" for x in gaps] or ["  - пробелов нет"]),
            "- новая дата обновления — только при изменении по существу: гейт статьи сверит текст с эталоном;", ""]
    body = [
        "## Задача",
        "",
        f"Составить бриф статьи на тему «{topic['topic']}» ({tid}, направление «{topic.get('direction', '')}») "
        "и записать его в brief.yaml по схеме выше. Текст статьи не писать: его пишут после утверждения брифа "
        "человеком.",
        "",
        *refresh_lines,
        "## Формулировки темы",
        *[f"- {f}" for f in topic["formulations"]],
        "",
        "## Запросы кластера — из них вопросы FAQ (`from_query` — дословно один из них)",
        *[f"- {q}" for q in queries],
        "",
        f"## Разбор топа ({top.get('serp_as_of')}): {'полный' if top.get('complete') else 'неполный'}",
        f"- объём: {(n.get('volume_chars') or {}).get('value', '—')} знаков; картинок: "
        f"{(n.get('images') or {}).get('value', '—')}; таблица: "
        f"{'нужна' if (n.get('table') or {}).get('need') else 'не нужна'}",
        *[f"- пробел: {gap['heading']} ({gap['source']})" for gap in top.get("gaps", [])[:20]],
        f"- фактура: {(top.get('facts') or {}).get('status', '—')}",
        f"- полный разбор: `{top_path.with_suffix('.md')}`",
        "",
        "## Автор — первый подходящий",
        *[f"- {a['name']}: {a['role']}, {a['experience']}; в этом месяце {a['used_this_month']} из "
          f"{a['month_limit']}" for a in authors],
        *[f"- ! {note}" for note in author_notes],
        "",
        "## Кандидаты перелинковки (по телам статей) — `links` и `read_also` только из них",
        *[f"- {c['url']} — {c['title'] or c['kind']} (вес {c['weight']})" for c in links],
        "",
        "## Правила",
        f"- title {g['title_length'][0]}–{g['title_length'][1]} знаков, description {g['description_length'][0]}–"
        f"{g['description_length'][1]}; slug латиницей через дефис;",
        "- H1 и H2 не берут целевые ключи чужих страниц сайта (проверит код);",
        f"- FAQ — {g['faq_questions'][0]}–{g['faq_questions'][1]} вопросов человеческим языком: запрос «X какие» → "
        "«Какие бывают X?»; вопрос начинается с вопросительного слова и кончается «?»;",
        f"- мост к продукту: место, доля {instance.p['brief']['bridge_share'][0]}–"
        f"{instance.p['brief']['bridge_share'][1]}, граница честности — обязательна;",
        "- призыв к действию — обычным блоком, не заголовком; у каждого раздела — подзаголовок;",
        "- угол — чего нет у лидеров выдачи (пробелы выше); правила тона и стандарты проекта — в паспорте проекта.",
    ]
    path = tasks.write_task(instance, tid, "planner", "brief", "\n".join(body), result=approval.BRIEF,
                            schema=schema_text())
    return {"task": str(path), "authors": authors, "links": len(links), "notes": author_notes}


def _starts_with_question(q: str) -> bool:
    """Вопросительное слово первым («Какие…»), после предлога («В каком…») или «X ли» («Опасен ли…»)."""
    words = re.findall(r"[а-яёa-z]+", q.lower())
    if not words:
        return False
    first = words[1] if words[0] in PREPOSITIONS and len(words) > 1 else words[0]
    return first.startswith(QUESTION_STARTS) or (len(words) > 1 and words[1] == "ли")


def check(instance, tid: str) -> dict:
    from .prepare import topic_of
    topic = topic_of(instance, tid)
    b = load_brief(instance, tid)
    p, g = instance.p, instance.p["gate_article"]
    norm = instance.norm
    errors, warnings = [], []
    for name, kind in (("h1", str), ("title", str), ("description", str), ("slug", str), ("angle", str),
                       ("level", str), ("sections", list), ("faq", list), ("author", str), ("links", list),
                       ("read_also", list), ("bridge", dict), ("cta_place", str)):
        if not isinstance(b.get(name), kind):
            errors.append(f"поле «{name}»: нет или не {kind.__name__}")
    if errors:
        return {"errors": errors, "warnings": warnings}
    lo, hi = g["title_length"]
    if not lo <= len(b["title"]) <= hi:
        errors.append(f"title {len(b['title'])} знаков, норма {lo}–{hi}")
    lo, hi = g["description_length"]
    if not lo <= len(b["description"]) <= hi:
        errors.append(f"description {len(b['description'])} знаков, норма {lo}–{hi}")
    if not SLUG.match(b["slug"]):
        errors.append(f"slug «{b['slug']}»: латиница нижнего регистра, цифры, одиночные дефисы")
    own = topic.get("slug") if topic.get("type") == "refresh" else None
    if own and b["slug"] != own:
        errors.append(f"slug «{b['slug']}»: у правки страницы slug — «{own}», как у живой страницы")
    if b["level"] not in LEVELS:
        errors.append(f"level «{b['level']}»: одно из {LEVELS}")
    h2 = [s.get("h2", "") for s in b["sections"] if isinstance(s, dict)]
    h3 = [x for s in b["sections"] if isinstance(s, dict) for x in (s.get("h3") or [])]
    if len(h2) < 2 or any(not x for x in h2):
        errors.append("разделов H2 меньше двух или есть раздел без заголовка")
    t = p["thresholds"]["title_overlap"]
    path = instance.file("demand_map")
    if path.is_file():
        from ..core.foreign import foreign_hits
        pages = store.read_json(path, "demand_map")["records"]
        heads = [(h, norm.key(h)) for h in [b["title"], b["h1"], *h2]]
        phrases = [(h, norm.key(h)) for h in [b["h1"], *h2, *h3]]
        taken, mentioned = foreign_hits(pages, heads, phrases, set(), norm, t, b["slug"])
        seen = set()
        for h in taken:
            if h.page["id"] not in seen:
                seen.add(h.page["id"])
                errors.append(f"заголовок «{h.phrase}» таргетирует ключ «{h.target}» страницы {h.page['url']}")
        _add_heading_links(instance, tid, [h for h in mentioned if h.page["id"] not in seen], warnings)
    else:
        warnings.append("карты спроса нет — чужие целевые ключи не проверены (`map build`)")
    for head in (b["title"], b["h1"]):
        key = norm.key(head)
        best = max(((title_similarity(key, item.title_key), item.page.title) for item in instance.index().pages
                    if item.page.slug != own),
                   default=(0.0, ""))
        if best[0] >= t:
            errors.append(f"заголовок «{head}» почти дублирует статью «{best[1]}» ({best[0]:.2f} ≥ {t})")
            break
    queries = cluster_queries(instance, topic)
    lo, hi = g["faq_questions"]
    if not lo <= len(b["faq"]) <= hi:
        errors.append(f"вопросов FAQ {len(b['faq'])}, норма {lo}–{hi}")
    for item in b["faq"]:
        q, src = str((item or {}).get("q", "")), str((item or {}).get("from_query", ""))
        if src not in queries:
            errors.append(f"FAQ «{q}»: from_query «{src}» не из запросов кластера")
        elif norm.key(src).similarity(norm.key(q)) < 1.0:
            errors.append(f"FAQ «{q}»: нет основ своего запроса «{src}»")
        if not _starts_with_question(q):
            errors.append(f"FAQ «{q}»: начинается не с вопросительного слова")
        if not q.strip().endswith("?"):
            errors.append(f"FAQ «{q}»: не кончается знаком вопроса")
    context = _context(instance, tid)
    allowed = {norm_url(c["url"]) for c in context.get("links", [])} | \
              {c["slug"] for c in context.get("links", [])}
    for field in ("links", "read_also"):
        for link in b[field]:
            if norm_url(str(link)) not in allowed and slug_of(str(link)) not in allowed:
                warnings.append(f"{field}: {link} — не из кандидатов перелинковки")
    names = {a["name"]: a for a in p["brief"]["authors"]}
    if b["author"] not in names:
        errors.append(f"автор «{b['author']}» не из brief.authors профиля")
    else:
        used = _month_load(instance).get(b["author"], 0)
        if used >= names[b["author"]]["month_limit"] and not (instance.brief_dir(tid) / approval.APPROVAL).is_file():
            warnings.append(f"автор {b['author']}: в этом месяце уже {used} при пределе "
                            f"{names[b['author']]['month_limit']} — взять следующего по списку")
    bridge = b["bridge"]
    lo_s, hi_s = p["brief"]["bridge_share"]
    share = bridge.get("share")
    if not isinstance(share, (int, float)) or not lo_s <= share <= hi_s:
        errors.append(f"bridge.share {share!r}: доля моста {lo_s}–{hi_s}")
    if not str(bridge.get("place") or "").strip():
        errors.append("bridge.place пуст: где мост к продукту")
    if not str(bridge.get("honesty_limit") or "").strip():
        errors.append("bridge.honesty_limit пуст: без границы честности бриф не утверждается")
    cta = b["cta_place"].strip()
    if not cta:
        errors.append("cta_place пуст")
    elif cta.startswith("#") or cta in h2 + h3:
        errors.append("cta_place — заголовок: призыв к действию ставится обычным блоком")
    return {"errors": errors, "warnings": warnings}


def _add_heading_links(instance, tid: str, hits, warnings: list[str]):
    """Н2: страницы, чей целевой ключ задевает подзаголовок брифа, — в кандидаты перелинковки «по подзаголовку».

    Гейт статьи потом потребует ссылку на них (`foreign_mentions`) — значит, она должна быть среди кандидатов. Это
    побочная запись проверки брифа (и `prepare approve`, который её вызывает) в `brief_context.json`."""
    if not hits:
        return
    path = instance.brief_dir(tid) / CONTEXT
    context = _context(instance, tid)
    links = context.setdefault("links", [])
    known = {c["url"] for c in links}
    added = 0
    for h in hits:
        warnings.append(f"нужна ссылка на {h.page['url']} — подзаголовок «{h.phrase}» задевает её ключ «{h.target}»")
        if h.page["url"] not in known:
            known.add(h.page["url"])
            links.append({"url": h.page["url"], "slug": h.page["id"], "title": h.page.get("title", ""), "weight": 0,
                          "kind": "по подзаголовку", "matched": 0})
            added += 1
    if added:
        path.write_text(json.dumps(context, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        warnings.append(f"кандидатов перелинковки «по подзаголовку» добавлено {added} → {CONTEXT}")


def _context(instance, tid: str) -> dict:
    path = instance.brief_dir(tid) / CONTEXT
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def approve(instance, tid: str, who: str) -> dict:
    result = check(instance, tid)
    if result["errors"]:
        raise GateRefused(f"бриф темы {tid} не прошёл проверку ({len(result['errors'])} ошибок) — не утверждается")
    record = approval.write(instance, tid, who)
    _agent_tokens(instance, tid, record["brief_sha256"])
    return record


def _agent_tokens(instance, tid: str, digest: str):
    """Т17.3, Р21: работа агента над брифом — строкой расхода; токены не сообщены — «нет данных», а не ноль.

    Одна строка на редакцию брифа: повторное утверждение той же редакции её не дублирует."""
    from ..core import spend
    from ..core.number import FACT, NO_DATA
    source = f"бриф {digest[:12]}"
    if any(r["id"] == tid and r["kind"] == "tokens" and r["source"] == source for r in spend.rows(instance)):
        return
    tokens = load_brief(instance, tid).get("tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
        spend.add(instance, tid, "бриф", "tokens", tokens, FACT, source)
    else:
        spend.add(instance, tid, "бриф", "tokens", None, NO_DATA, source)


def register_actions(actions):
    from ..cli import add_profile
    for name, text in (("brief", "задание роли planner на бриф"), ("check", "проверить бриф кодом"),
                       ("approve", "утвердить бриф (человек)")):
        p = actions.add_parser(name, help=text)
        add_profile(p)
        p.add_argument("id")
        if name == "approve":
            p.add_argument("--who", required=True, help="кто утверждает")
        p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "brief":
        out = make_task(instance, args.id)
        print(f"задание: {out['task']}; авторов-кандидатов {len(out['authors'])}, кандидатов перелинковки "
              f"{out['links']}")
        for note in out["notes"]:
            print(f"  ! {note}")
        return 0
    if args.action == "check":
        out = check(instance, args.id)
        for line in out["errors"]:
            print(f"  ✗ {line}")
        for line in out["warnings"]:
            print(f"  ! {line} (предупреждение)")
        print(f"Бриф {args.id}: " + ("ошибок нет" if not out["errors"] else f"ошибок {len(out['errors'])}")
              + f", предупреждений {len(out['warnings'])}")
        return 1 if out["errors"] else 0
    record = approve(instance, args.id, args.who)
    print(f"бриф {args.id} утверждён: {record['who']}, {record['at']}")
    return 0
