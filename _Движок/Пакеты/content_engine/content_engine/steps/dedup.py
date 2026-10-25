"""Анти-дубли до модели — семь слоёв (Т10).

Кандидат — тема и не меньше трёх формулировок: одна удобная формулировка обманывает проверку. Слои считаются все,
даже после первого сработавшего блокирующего: отчёт показывает каждый. Пара «кандидат — страница» из белого списка
не срабатывает ни на каком слое. Слои, которым нужна выдача, без неё пропускаются с пометкой — такой гейт неполный.

| № | Слой | Итог |
| 1 | смысловой ключ против написанного, очереди и броней | пересекается |
| 2 | тексты статей: статья целиком и раздел | пересекается / спорно |
| 3 | общая выдача с головным запросом нашей страницы | пересекается |
| 4 | наша страница в топ-10 — по лесенке ценности | пересекается / спорно |
| 5 | продающая страница | пересекается |
| 6 | лимит живых статей на головной термин | пересекается |
| 7 | почти-дубль заголовка | пересекается |
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..adapters.serp import OK
from ..core import store
from ..core.normalize import Key
from ..core.quota import fetch_serp
from ..core.serp_cache import SerpCache
from ..core.urls import is_own, norm_url, slug_of

BLOCK, WARN, PASS, SKIPPED, NO_DATA = "пересекается", "спорно", "чисто", "пропущен", "нет данных"
LAYER_NAMES = {1: "смысловой ключ", 2: "тексты статей", 3: "общая выдача", 4: "наша страница в топ-10",
               5: "продающая страница", 6: "лимит на головной термин", 7: "почти-дубль заголовка"}
BODY_CAP = 40
HEADING_BONUS = 0.5
MIN_FORMULATIONS = 3


@dataclass
class LayerResult:
    layer: int
    outcome: str
    reason: str = ""
    page: str = ""             # адрес или slug страницы, с которой пересеклась тема
    section: str = ""          # раздел статьи (слой 2)
    numbers: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return LAYER_NAMES.get(self.layer, "тип выдачи")

    def to_dict(self) -> dict:
        return {"layer": self.layer, "name": self.name, "outcome": self.outcome, "reason": self.reason,
                "page": self.page, "section": self.section, "numbers": self.numbers}


def concepts_of(keys: list[Key]) -> list[frozenset]:
    """Основы всех формулировок без повторов (словоформы одного слова — одна основа)."""
    parts: list[frozenset] = []
    for key in keys:
        for part in key.parts:
            if not any(part & seen for seen in parts):
                parts.append(part)
    return parts


def _label(part: frozenset) -> str:
    return max(part, key=lambda v: (len(v), v))


def layer_texts(index, formulations: list[str], thresholds: dict, *, skip_slugs=frozenset()) -> LayerResult:
    """Слой 2: два измерителя по полным текстам опубликованного.

    Статья целиком — как у проверенного чекера: основы всех формулировок, статья должна содержать каждую; вес —
    сумма вхождений с потолком 40 на основу. Раздел — по каждой формулировке отдельно: раздел (подзаголовок и его
    текст), где есть все её основы; вес — наименьшее число вхождений её основы в разделе плюс половина числа её основ
    в подзаголовке. Сумма вхождений здесь не годится: её набирают общие слова ниши и длинные разделы без
    подзаголовков, а раздел, который тему закрывает, говорит о каждой её основе и обычно называет её в подзаголовке
    (ранний замер на корпусе первого потребителя). Бонус за подзаголовок — половина: целый перевешивал, когда в
    подзаголовке общие слова, а отличительное слово темы встречалось в разделе один раз. Отчёт называет статью и раздел.
    """
    norm = index.normalizer
    pairs = [(f, norm.key(f)) for f in formulations]
    pairs = [(f, k) for f, k in pairs if k]          # формулировка из одних стоп-слов ключа не даёт
    keys = [k for _, k in pairs]
    concepts = concepts_of(keys)
    body_hits, section_hits = [], []
    for item in index.pages:
        if item.page.slug in skip_slugs:
            continue
        counts = [index.count(item.counts, part) for part in concepts]
        if concepts and all(counts):
            weight = sum(min(c, BODY_CAP) for c in counts)
            body_hits.append({"slug": item.page.slug, "url": item.page.url, "title": item.page.title,
                              "weight": weight,
                              "hits": {_label(p): c for p, c in zip(concepts, counts)}})
        for section in item.sections:
            for formulation, key in pairs:
                section_counts = [index.count(section.counts, part) for part in key.parts]
                if all(section_counts):
                    heading = norm_counts(norm, section.heading)
                    in_heading = sum(1 for p in key.parts if index.count(heading, p))
                    section_hits.append({
                        "slug": item.page.slug, "url": item.page.url, "title": item.page.title,
                        "section": section.heading or "(вступление)", "formulation": formulation,
                        "weight": min(section_counts) + HEADING_BONUS * in_heading, "least": min(section_counts),
                        "in_heading": in_heading, "total": sum(section_counts),
                        "hits": {_label(p): c for p, c in zip(key.parts, section_counts)}})
    body_hits.sort(key=lambda h: (-h["weight"], h["slug"]))
    section_hits.sort(key=lambda h: (-h["weight"], -h["total"], h["slug"]))
    numbers = {"concepts": [_label(p) for p in concepts], "body_top": body_hits[:5], "section_top": section_hits[:5]}
    t = thresholds
    verdicts = []
    if body_hits:
        top = body_hits[0]
        if top["weight"] >= t["body_block"]:
            verdicts.append((BLOCK, f"статья целиком: вес {top['weight']} ≥ {t['body_block']}", top, ""))
        elif top["weight"] >= t["body_warn"]:
            verdicts.append((WARN, f"статья целиком: вес {top['weight']} ≥ {t['body_warn']}", top, ""))
    if section_hits:
        top = section_hits[0]
        if t["section_block"] is None:
            numbers["section_note"] = "пороги раздела не заданы — измеритель только в отчёте"
        elif top["weight"] >= t["section_block"]:
            verdicts.append((BLOCK, f"раздел: вес {top['weight']} ≥ {t['section_block']} "
                                    f"по формулировке «{top['formulation']}»", top, top["section"]))
        elif top["weight"] >= t["section_warn"]:
            verdicts.append((WARN, f"раздел: вес {top['weight']} ≥ {t['section_warn']} "
                                   f"по формулировке «{top['formulation']}»", top, top["section"]))
    for outcome in (BLOCK, WARN):
        chosen = [v for v in verdicts if v[0] == outcome]
        if chosen:
            # из двух измерителей с одинаковым итогом называем тот, что указывает раздел: он точнее
            _, reason, hit, section = sorted(chosen, key=lambda v: not v[3])[0]
            if section and len(chosen) > 1:
                reason = "; ".join(v[1] for v in chosen)
            return LayerResult(2, outcome, reason, hit["url"], section, numbers)
    return LayerResult(2, PASS, "в текстах статей тема не закрыта", numbers=numbers)


class Context:
    """Что нужно слоям: инстанс, индекс текстов, карта спроса, очередь, брони, белый список, выдача."""

    def __init__(self, instance, *, offline: bool = False, fresh: bool = False, max_quota: int | None = None,
                 cache_only: bool = False):
        self.instance = instance
        self.offline = offline
        self.cache_only = cache_only        # предфильтр очереди: выдача только из кэша, новых запросов нет (Т12.3)
        self.fresh = fresh                  # выдача формулировок — только снятая сегодня (взятие темы, Т11.4)
        self.max_quota = max_quota
        self._map = None
        self._serp: dict[tuple, dict] = {}
        self._page_tops = None
        self.serp_plans: list = []
        self.queue_topics = None            # темы очереди в памяти (сборка очереди): слой 1 видит и ещё не записанные

    @property
    def p(self) -> dict:
        return self.instance.p

    @property
    def index(self):
        return self.instance.index()

    @property
    def norm(self):
        return self.instance.norm

    def demand_map(self) -> dict | None:
        if self._map is None:
            path = self.instance.file("demand_map")
            self._map = store.read_json(path, "demand_map") if path.is_file() else {}
        return self._map or None

    def pages(self) -> dict[str, dict]:
        data = self.demand_map()
        return {pg["id"]: pg for pg in data["records"]} if data else {}

    def whitelisted(self, candidate) -> set[str]:
        """Страницы из белого списка для этой темы (Т10.1): slug или адрес."""
        records = store.read_json(self.instance.file("whitelist"), "whitelist", missing_ok=True)["records"]
        key = self.norm.key(candidate.topic).canonical()
        out = set()
        for r in records:
            if r["topic"] == candidate.topic or self.norm.key(r["topic"]).canonical() == key:
                out.add(r["page"])
                out.add(norm_url(r["page"]))
                if slug_of(r["page"]):
                    out.add(slug_of(r["page"]))   # слои сверяют статьи корпуса по slug
        return out

    def serp(self, phrases: list[str]):
        """{база: {фраза: SerpResult}} через квоту и кэш; None — выдача не запрашивается (--offline)."""
        if self.offline:
            return None
        cache_key = tuple(phrases)
        if cache_key not in self._serp:
            result, plan = fetch_serp(self.instance, phrases, fresh_only=self.fresh, max_quota=self.max_quota,
                                      offline=self.cache_only)
            self.serp_plans.append(plan)
            self._serp[cache_key] = result
        return self._serp[cache_key]

    def page_tops(self) -> dict[str, set[str]]:
        """Адреса топ-10 по головным запросам каждой нашей страницы — из кэша (их снимает `map build`)."""
        if self._page_tops is None:
            cache = SerpCache(self.instance.root, self.p["thresholds"]["serp_cache_days"], self.instance.today)
            tops: dict[str, set[str]] = {}
            for pid, pg in self.pages().items():
                urls = set()
                for query in pg["head_queries"]:
                    for base in self.p["search"]["serp_bases"]:
                        hit = cache.get(query, base)
                        if hit is not None and hit.status == OK:
                            urls.update(u for u, _ in hit.top)
                if urls:
                    tops[pid] = urls
            self._page_tops = tops
        return self._page_tops


def _skip(page: dict, skip: set[str]) -> bool:
    return page["id"] in skip or norm_url(page["url"]) in skip


def layer_key(ctx, cand, skip) -> LayerResult:
    """Слой 1: ключ темы против написанного (заголовок и подзаголовки), очереди и активных броней."""
    from . import queue as queue_mod
    from . import reservations
    t = ctx.p["thresholds"]["key_overlap"]
    key = cand.key(ctx.norm)
    for r in reservations.active(ctx.instance):
        if cand.queue_id and r.get("queue_id") == cand.queue_id:
            continue
        same_cluster = bool(cand.cluster_id) and r["cluster_id"] == cand.cluster_id
        other = Key.from_list(r["key"]) if r["key"] else ctx.norm.key(r["topic"])
        sim = 1.0 if same_cluster else key.similarity(other)
        if sim >= t:
            return LayerResult(1, BLOCK, f"забронирован темой «{r['topic']}» (бронь {r['id']} до {r['expires']})",
                               numbers={"similarity": round(sim, 3), "reservation": r["id"]})
    best = (0.0, "", "")
    for item in ctx.index.pages:
        if item.page.slug in skip or item.page.slug in cand.skip_slugs:
            continue
        sim = key.similarity(item.key)
        if sim > best[0]:
            best = (sim, item.page.url, f"статья «{item.page.title}»")
    for topic in (ctx.queue_topics if ctx.queue_topics is not None else queue_mod.load(ctx.instance)):
        if topic["id"] == cand.queue_id or topic["status"] in (queue_mod.REJECTED, queue_mod.PUBLISHED):
            continue
        sim = key.similarity(ctx.norm.key(topic["topic"]))
        if sim > best[0]:
            best = (sim, "", f"тема очереди {topic['id']} «{topic['topic']}»")
    numbers = {"similarity": round(best[0], 3), "with": best[2]}
    if best[0] >= t:
        return LayerResult(1, BLOCK, f"ключ совпадает на {best[0]:.2f} ≥ {t}: {best[2]}", best[1], numbers=numbers)
    return LayerResult(1, PASS, f"наибольшее сходство ключа {best[0]:.2f}: {best[2] or '—'}", numbers=numbers)


def _formulation_tops(ctx, cand):
    serp = ctx.serp(cand.formulations)
    if serp is None:
        return None, "выдача не запрашивалась (--offline)"
    tops, missing = {}, []
    for f in cand.formulations:
        urls, found, have = set(), False, False
        for base_results in serp.values():
            r = base_results.get(f)
            if r is None:
                continue
            have = True
            if r.status == OK:
                found = True
                urls.update(u for u, _ in r.top)
        if not have:
            missing.append(f)
        elif found:
            tops[f] = urls
    if missing:
        return None, f"нет выдачи по {len(missing)} формулировкам — не хватило квоты"
    return tops, ""


def layer_serp(ctx, cand, skip) -> LayerResult:
    """Слой 3: топ-10 формулировки делит не меньше `serp_common_urls` адресов с топ-10 головного запроса нашей страницы."""
    if ctx.demand_map() is None:
        return LayerResult(3, NO_DATA, "карты спроса нет — `map build`")
    tops, why = _formulation_tops(ctx, cand)
    if tops is None:
        return LayerResult(3, SKIPPED, why)
    if not tops:
        return LayerResult(3, PASS, "формулировок нет в базе выдачи")
    need = ctx.p["thresholds"]["serp_common_urls"]
    pages = ctx.pages()
    if not ctx.page_tops():
        return LayerResult(3, NO_DATA, "выдачи головных запросов наших страниц нет в кэше — `map build`")
    best = (0, "", "")
    for f, urls in tops.items():
        for pid, page_urls in ctx.page_tops().items():
            if _skip(pages[pid], skip) or pid in cand.skip_slugs:
                continue
            common = len(urls & page_urls)
            if common > best[0]:
                best = (common, pages[pid]["url"], f)
    numbers = {"common": best[0], "formulation": best[2],
               "pages_with_serp": f"{len(ctx.page_tops())} из {len(pages)}"}
    if best[0] >= need:
        return LayerResult(3, BLOCK, f"общих адресов в топ-10: {best[0]} ≥ {need} (формулировка «{best[2]}»)",
                           best[1], numbers=numbers)
    return LayerResult(3, PASS, f"наибольшее число общих адресов: {best[0]}", numbers=numbers)


def layer_own_top(ctx, cand, skip) -> LayerResult:
    """Слой 4: наша страница в топ-10 по формулировке — решает лесенка ценности страницы (Т4.4)."""
    serp = ctx.serp(cand.formulations)
    if serp is None:
        return LayerResult(4, SKIPPED, "выдача не запрашивалась (--offline)")
    domains = ctx.p["site"]["domains"]
    hits = []
    for base_results in serp.values():
        for f in cand.formulations:
            r = base_results.get(f)
            if r is None:
                return LayerResult(4, SKIPPED, "нет выдачи по формулировке — не хватило квоты")
            for url, pos in r.top:
                slug = slug_of(url)
                if is_own(url, domains) and url not in skip and slug not in skip and slug not in cand.skip_slugs:
                    hits.append((pos, url, f))
    if not hits:
        return LayerResult(4, PASS, "наших страниц в топ-10 нет")
    pos, url, f = min(hits)
    value = _page_value(ctx, url)
    step, money = value["step"], value["money"]
    numbers = {"position": pos, "formulation": f, "step": step, "money": money}
    days = ctx.p["thresholds"]["money_days"]
    if step in (1, 2) and (money["value"] or 0) > 0:
        return LayerResult(4, BLOCK, f"наша страница на {pos}-м месте и зарабатывает: {money['value']:.0f} "
                                     f"за {days} дней ({money['status']})", url, numbers=numbers)
    if step in (1, 2):
        return LayerResult(4, WARN, f"наша страница на {pos}-м месте без денег — улучшать её или писать новую, "
                                    "решает человек", url, numbers=numbers)
    return LayerResult(4, WARN, f"наша страница на {pos}-м месте; ценность — {money['note'] or money['status']}: "
                                "proxy и отсутствие данных тему не блокируют", url, numbers=numbers)


def _page_value(ctx, url: str) -> dict:
    pages = ctx.pages()
    page = pages.get(slug_of(url)) or pages.get(norm_url(url))
    if page and page.get("value"):
        return page["value"]
    from ..adapters.value import PageValue
    v = PageValue(ctx.p, ctx.instance.root, ctx.instance.today).value(url)
    return {"step": v["step"], "money": v["money"].to_dict()}


def layer_sales(ctx, cand, skip) -> LayerResult:
    """Слой 5: владелец кластера — продающая страница, или ключ темы близок к ключам продающей страницы."""
    data = ctx.demand_map()
    if data is None:
        return LayerResult(5, NO_DATA, "карты спроса нет — `map build`")
    pages = ctx.pages()
    if cand.cluster_id:
        c = next((c for c in data["clusters"] if c["id"] == cand.cluster_id), None)
        if c and c["owner"] and pages[c["owner"]]["type"] == "sales" and not _skip(pages[c["owner"]], skip):
            return LayerResult(5, BLOCK, "владелец кластера — продающая страница", pages[c["owner"]]["url"])
    t = ctx.p["thresholds"]["sales_overlap"]
    key = cand.key(ctx.norm)
    best = (0.0, "", "")
    for page in pages.values():
        if page["type"] != "sales" or _skip(page, skip):
            continue
        for q in page["target_keys"] + page["head_queries"]:
            sim = key.similarity(ctx.norm.key(q))
            if sim > best[0]:
                best = (sim, page["url"], q)
    numbers = {"similarity": round(best[0], 3), "query": best[2]}
    if best[0] >= t:
        return LayerResult(5, BLOCK, f"ключ темы близок к запросу продающей страницы «{best[2]}»: {best[0]:.2f} ≥ {t}",
                           best[1], numbers=numbers)
    return LayerResult(5, PASS, f"наибольшее сходство с продающими: {best[0]:.2f}", numbers=numbers)


def layer_head_cap(ctx, cand, skip) -> LayerResult:
    """Слой 6: живых статей, в заголовке которых есть все основы ключа темы (головной термин), не больше лимита."""
    cap = ctx.p["thresholds"]["head_term_cap"]
    key = cand.key(ctx.norm)
    same = [item.page.slug for item in ctx.index.pages
            if item.page.slug not in skip and item.page.slug not in cand.skip_slugs
            and key.similarity(item.title_key) == 1.0]
    numbers = {"articles": len(same), "examples": same[:cap + 1]}
    if len(same) >= cap:
        return LayerResult(6, BLOCK, f"живых статей с тем же головным термином: {len(same)} ≥ {cap}", numbers=numbers)
    return LayerResult(6, PASS, f"статей с тем же головным термином: {len(same)}", numbers=numbers)


def title_similarity(a: Key, b: Key) -> float:
    """Почти-дубль заголовка — сходство в обе стороны: короткий заголовок внутри длинного — ещё не дубль."""
    return min(a.similarity(b), b.similarity(a))


def layer_title(ctx, cand, skip) -> LayerResult:
    """Слой 7: заголовок темы почти совпадает с заголовком живой статьи."""
    t = ctx.p["thresholds"]["title_overlap"]
    key = cand.key(ctx.norm)
    best = (0.0, "", "")
    for item in ctx.index.pages:
        if item.page.slug in skip or item.page.slug in cand.skip_slugs:
            continue
        sim = title_similarity(key, item.title_key)
        if sim > best[0]:
            best = (sim, item.page.url, item.page.title)
    numbers = {"similarity": round(best[0], 3), "title": best[2]}
    if best[0] >= t:
        return LayerResult(7, BLOCK, f"заголовок почти совпадает со статьёй «{best[2]}»: {best[0]:.2f} ≥ {t}",
                           best[1], numbers=numbers)
    return LayerResult(7, PASS, f"наибольшее сходство заголовков {best[0]:.2f}", numbers=numbers)


def layer_2(ctx, cand, skip) -> LayerResult:
    return layer_texts(ctx.index, cand.formulations, ctx.p["thresholds"], skip_slugs=cand.skip_slugs | skip)


LAYERS = {1: layer_key, 2: layer_2, 3: layer_serp, 4: layer_own_top, 5: layer_sales, 6: layer_head_cap,
          7: layer_title}


def run_layers(ctx, candidate, layers=None) -> list[LayerResult]:
    skip = ctx.whitelisted(candidate)
    out = []
    for number in sorted(LAYER_NAMES):
        if layers is not None and number not in layers:
            continue
        out.append(LAYERS[number](ctx, candidate, skip))
    return out


def serp_type(ctx, candidate) -> dict:
    """Тип выдачи (Т11.2): магазинов в топ-10 по головной формулировке не меньше `commercial_min` — «коммерческая»."""
    serp = ctx.serp(candidate.formulations)
    if serp is None:
        return {"outcome": SKIPPED, "reason": "выдача не запрашивалась (--offline)"}
    head = candidate.formulations[0]
    urls, have = set(), False
    for base_results in serp.values():
        r = base_results.get(head)
        if r is not None:
            have = True
            if r.status == OK:
                urls.update(u for u, _ in r.top)
    if not have:
        return {"outcome": SKIPPED, "reason": "нет выдачи по головной формулировке — не хватило квоты"}
    from .demand_map import commercial_count
    n = commercial_count(ctx.instance, urls)
    need = ctx.p["thresholds"]["commercial_min"]
    if n >= need:
        return {"outcome": "коммерческая", "reason": f"магазинов в топ-10: {n} ≥ {need} — в очередь продающих страниц"}
    return {"outcome": PASS, "reason": f"магазинов в топ-10: {n}"}


def norm_counts(norm, text: str):
    from collections import Counter
    return Counter(norm.tokens(text))
