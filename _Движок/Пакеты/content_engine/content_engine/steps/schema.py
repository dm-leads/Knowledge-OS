"""Разметка из текста и брифа (Т20.1): Article, FAQPage, BreadcrumbList.

`build(статья, бриф, профиль, день)` — граф JSON-LD: заголовок — H1 текста, описание и автор — из брифа,
`datePublished` и `dateModified` — день сборки, вопросы и ответы — из раздела FAQ текста, хлебные крошки — домен →
раздел → статья. Ключ `slug` рядом с графом — для сверки разметки со статьёй. Формат на сайте — `assemble.schema_format`:
JSON-LD кладётся скриптом, микроразметку ставит сборщик атрибутами из того же графа. Согласованность с текстом
проверяет проверка `schema` гейта статьи — и для JSON, и для HTML с микроразметкой.
"""
from __future__ import annotations

import re

from ..adapters.corpus import HEADING, split_front_matter


def faq_pairs(body: str, faq_heading: str) -> list[tuple[str, str]]:
    """Пары «вопрос — ответ» из раздела FAQ: вопрос — строка, которая кончается «?», ответ — строки до следующего."""
    lines = body.splitlines()
    start = next((i for i, l in enumerate(lines) if (m := HEADING.match(l)) and len(m.group(1)) >= 2
                  and m.group(2).lower().startswith(faq_heading.lower())), None)
    if start is None:
        return []
    pairs, question, answer = [], None, []
    for line in lines[start + 1:]:
        m = HEADING.match(line)
        if m and len(m.group(1)) <= 2:
            break
        text = (m.group(2) if m else line).strip().strip("*_ ").strip()
        if text.endswith("?"):
            if question:
                pairs.append((question, " ".join(answer).strip()))
            question, answer = text, []
        elif question and text:
            answer.append(text)
    if question:
        pairs.append((question, " ".join(answer).strip()))
    return pairs


def h1_of(body: str) -> str:
    return next((m.group(2).strip() for line in body.splitlines() if (m := HEADING.match(line))
                 and m.group(1) == "#"), "")


def canonical(profile: dict, slug: str) -> str:
    domain = profile["site"]["domains"][0]
    section = profile["site"]["article_sections"][0] if profile["site"]["article_sections"] else ""
    return f"https://{domain}/{section}/{slug}" if section else f"https://{domain}/{slug}"


def build(article_text: str, brief: dict, profile: dict, day: str) -> dict:
    meta, body = split_front_matter(article_text)
    h1 = h1_of(body) or str(brief.get("h1") or "")
    slug = str(brief.get("slug") or meta.get("slug") or "")
    url = canonical(profile, slug)
    domain = profile["site"]["domains"][0]
    section = profile["site"]["article_sections"][0] if profile["site"]["article_sections"] else ""
    author = brief.get("author")
    author = author.get("name", "") if isinstance(author, dict) else str(author or "")
    graph = [{"@type": "Article", "headline": h1, "description": str(brief.get("description") or ""),
              "author": {"@type": "Person", "name": author}, "datePublished": day, "dateModified": day,
              "mainEntityOfPage": url}]
    pairs = faq_pairs(body, profile["gate_article"]["faq_heading"])
    if pairs:
        graph.append({"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs]})
    crumbs = [{"@type": "ListItem", "position": 1, "name": domain, "item": f"https://{domain}/"}]
    if section:
        crumbs.append({"@type": "ListItem", "position": 2, "name": section, "item": f"https://{domain}/{section}/"})
    crumbs.append({"@type": "ListItem", "position": len(crumbs) + 1, "name": h1, "item": url})
    graph.append({"@type": "BreadcrumbList", "itemListElement": crumbs})
    return {"@context": "https://schema.org", "@graph": graph, "slug": slug}


def problems(nodes: list[dict], *, schema_slug: str, title: str, h1s: list[str], slug: str, faq_text: str) -> list[str]:
    """Сверка разметки с текстом — одна для JSON-LD и микроразметки (Т20.1)."""
    out = []
    article = next((n for n in nodes if isinstance(n, dict) and n.get("@type") in
                    ("Article", "BlogPosting", "NewsArticle")), None)
    faqp = next((n for n in nodes if isinstance(n, dict) and n.get("@type") == "FAQPage"), None)
    if article is None:
        out.append("нет узла Article")
    else:
        headline = article.get("headline", "")
        headline = (headline[0] if isinstance(headline, list) else headline) or ""
        if headline.strip() not in {title.strip(), *(x.strip() for x in h1s)}:
            out.append("headline не совпадает с заголовком")
    if schema_slug and slug and schema_slug != slug:
        out.append("slug разметки не совпадает со статьёй")
    if faqp is not None:
        entities = faqp.get("mainEntity", [])
        entities = entities if isinstance(entities, list) else [entities]
        norm = lambda s: re.sub(r"\s+", " ", s or "").strip()           # noqa: E731
        lost = [q.get("name", "") for q in entities if isinstance(q, dict)
                and norm(q.get("name", "")) not in norm(faq_text)]
        if lost:
            out.append(f"вопросов FAQ нет в тексте: {len(lost)}")
    return out
