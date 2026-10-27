"""Проверки текста части 2 в гейте статьи (Т28.2, Т29.3–Т29.4, Т30): ритм, ИИ-интонация, объём, ссылки на нормы,
ссылки на свои статьи, прямые ответы под ИИ-выдачу, вычитка и факты, профиль ниши, alt и кредиты картинок.

Уровень каждой — `gate_article.levels`, как у проверок волны 1. Проверка, которой не хватает данных (направление
темы, отчёт вычитки), не «проходит», а пишет «не проверялось» с причиной.
"""
from __future__ import annotations

import json
import re
import statistics

from ..adapters.corpus import HEADING
from ..core.urls import is_own, norm_url, section_of, slug_of

LIST_LINE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)[^)]*\)|(?<![(\w])(https?://[^\s)<>\]]+)")
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)
SENTENCE = re.compile(r"(?<=[.!?…])\s+")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def paragraphs(body: str) -> list[str]:
    """Абзацы текста: блоки между пустыми строками без заголовков и списков."""
    out = []
    for block in re.split(r"\n\s*\n", body):
        lines = [l for l in block.splitlines() if l.strip() and not HEADING.match(l) and not LIST_LINE.match(l)
                 and not l.lstrip().startswith(("|", "!["))]
        if lines:
            out.append(" ".join(l.strip() for l in lines))
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"[0-9A-Za-zА-Яа-яЁё-]+", text)


def _cv(values: list[int]) -> float:
    values = [v for v in values if v]
    if len(values) < 2 or not statistics.mean(values):
        return 1.0
    return statistics.pstdev(values) / statistics.mean(values)


def plain_chars(body: str) -> int:
    text = MD_IMAGE.sub("", body)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = "\n".join(l for l in text.splitlines() if not HEADING.match(l))
    text = re.sub(r"[*_`#>|]", "", text)
    return len(re.sub(r"\s+", "", text))


def run(instance, add, results, *, body: str, sections: list, faq: list, brief: dict | None, topic_id: str | None,
        direction: str | None, article_sha: str | None, links: list[str]) -> None:
    p, g = instance.p, instance.p["gate_article"]
    levels = g["levels"]
    norm = instance.norm
    paras = paragraphs(body)

    def missing(name, why):
        if levels.get(name, "off") != "off":
            results.append({"check": name, "level": levels[name], "ok": False, "detail": f"не проверялось: {why}"})

    # ритм: ровная длина абзацев и предложений — признак машинного текста
    if len(paras) >= 5:
        sentences = [len(_words(s)) for para in paras for s in SENTENCE.split(para)]
        cv_p, cv_s = _cv([len(_words(x)) for x in paras]), _cv(sentences)
        add("rhythm", max(cv_p, cv_s) >= g["rhythm_min_cv"],
            f"разброс длины: абзацы {cv_p:.2f}, предложения {cv_s:.2f}; порог {g['rhythm_min_cv']}")
    else:
        add("rhythm", True, f"абзацев {len(paras)} — ритм не меряется")
    tone = [m.group(0) for rx in g["ai_tone_patterns"] for m in [re.search(rx, body, re.I | re.M)] if m]
    add("ai_tone", not tone, "шаблоны ИИ-интонации: " + "; ".join(f"«{t}»" for t in tone[:5]) if tone else
        "шаблонов ИИ-интонации нет" + ("" if g["ai_tone_patterns"] else " (список шаблонов в профиле пуст)"))
    # объём — в коридоре направления
    corridor = next((d["volume"] for d in p["directions"] if d["name"] == direction), None) if direction else None
    if corridor:
        chars = plain_chars(body)
        add("volume", corridor[0] <= chars <= corridor[1],
            f"объём {chars} знаков, коридор направления «{direction}» {corridor[0]}–{corridor[1]}")
    else:
        missing("volume", "направление темы неизвестно (статья не из очереди)")
    # нормы — со ссылкой в том же абзаце
    bare = []
    for para in paras:
        if any(re.search(rx, para) for rx in g["norm_patterns"]) and not MD_LINK.search(para):
            bare.append(para[:80])
    add("norm_links", not bare, "норма без ссылки: " + "; ".join(f"«{b}…»" for b in bare[:3]) if bare else
        "у каждой нормы есть ссылка")
    # ссылки на свои статьи — только на статьи корпуса или карты спроса
    domains = p["site"]["domains"]
    known = {item.page.slug for item in instance.index().pages}
    path = instance.file("demand_map")
    if path.is_file():
        from ..core import store
        known |= {page["id"] for page in store.read_json(path, "demand_map")["records"]}
    own = [u for u in links if (u.startswith("/") or is_own(u, domains))]
    stray = [u for u in own if section_of(u if not u.startswith("/") else domains[0] + u) in p["site"]["article_sections"]
             and slug_of(u) not in known]
    add("internal_links", not stray, "ссылки на статьи не из корпуса: " + ", ".join(stray[:5]) if stray else
        f"ссылок на свои статьи: {len(own)}")
    # прямой ответ в начале раздела и FAQ — под ИИ-ответы
    skip = (g["faq_heading"].lower(), p["corpus"]["toc_marker"].lower())
    weak = []
    for level, title, text in sections:
        if level != 2 or title.lower().startswith(skip):
            continue
        first = next(iter(paragraphs(text)), "")
        if not first:
            weak.append(f"«{title}» — нет абзаца в начале")
            continue
        n = len(_words(first))
        shared = norm.key(title).parts and any(part & set().union(*norm.key(first).parts) for part in norm.key(title).parts) \
            if norm.key(first).parts else False
        if n > g["answer_words"] or not shared:
            weak.append(f"«{title}» — " + (f"первый абзац {n} слов" if n > g["answer_words"] else
                                            "первый абзац не отвечает на заголовок"))
    if not faq:
        weak.append("нет FAQ")
    add("ai_answers", not weak, "; ".join(weak[:5]) if weak else "у каждого раздела прямой ответ в начале, FAQ на месте")
    # вычитка и факты
    if p["facts"]["enabled"]:
        problems = []
        if topic_id:
            review = instance.brief_dir(topic_id) / "review.json"
            if not review.is_file():
                problems.append("вычитки нет — `review task` и `review check`")
            else:
                from .review import text_digest
                r = json.loads(review.read_text(encoding="utf-8"))
                if r.get("article_sha256") != text_digest(instance.brief_dir(topic_id) / "article.md"):
                    problems.append("вычитка была по прежнему тексту — `review check` заново")
                elif not r.get("clean"):
                    problems.append("вычитка не закрыта: " + "; ".join(r.get("open", [])[:3]))
        from ..core import facts
        numbers = set(NUMBER.findall(body))
        stale = [f for f in facts.expired(instance) if str(f["value"]) in numbers]
        problems += [f"число {f['value']} — факт «{f['fact']}» просрочен (проверен {f['checked']})" for f in stale]
        add("facts", not problems, "; ".join(problems) if problems else "вычитка закрыта, просроченных фактов нет")
    else:
        add("facts", True, "библиотека фактов не подключена (facts.enabled: false)")
    # профиль ниши
    niche = p["niche"]
    nproblems = []
    external = {norm_url(u).split("/")[0] for u in links if u.startswith("http") and not is_own(u, domains)}
    if len(external) < niche["min_sources"]:
        nproblems.append(f"первоисточников {len(external)}, ниша «{niche['kind']}» требует {niche['min_sources']}")
    if niche["disclaimer"] and niche["disclaimer"].lower() not in body.lower():
        nproblems.append(f"нет обязательной оговорки «{niche['disclaimer']}»")
    if niche["level"] == "усиленный" and brief is not None and brief.get("level") != "усиленный":
        nproblems.append("ниша требует усиленного уровня текста, в брифе — обычный")
    add("niche", not nproblems, "; ".join(nproblems) if nproblems else f"требования ниши «{niche['kind']}» выполнены")
    # картинки: alt и кредиты
    no_alt = [url for alt, url in MD_IMAGE.findall(body) if not alt.strip()]
    no_alt += [tag[:60] for tag in HTML_IMG.findall(body) if not re.search(r'\balt\s*=\s*"[^"]+"', tag)]
    add("image_alt", not no_alt, "картинки без alt: " + ", ".join(no_alt[:5]) if no_alt else "у картинок есть alt")
    if topic_id and (instance.brief_dir(topic_id) / "images.json").is_file():
        plan = json.loads((instance.brief_dir(topic_id) / "images.json").read_text(encoding="utf-8"))
        bad = [i.get("place", "?") for i in plan.get("images", [])
               if i.get("kind") == "photo" and not (str(i.get("author") or "").strip() and str(i.get("license") or "").strip())]
        add("image_credits", not bad, "фото без автора или лицензии: " + ", ".join(map(str, bad)) if bad else
            "у фото из сети есть автор и лицензия")
    else:
        add("image_credits", True, "фото из сети нет (плана картинок нет)")
