"""Дрифт живых страниц (модуль Д1, Т34.2): эталонный снимок дня выкладки (Т22.6) против того, что сайт отдаёт сегодня.

Критично: ответ не 200, запрет индексации, canonical сменился или пропал, пропал H1, разметка не читается,
перенаправление. Важно: переписаны title, description или H1, пропала картинка для соцсетей, текст короче эталона больше
чем на `monitor.drift_shrink`. Сеть не ответила — «не проверено», а не критично (как «доехало», О1). Страница без эталона
(статьи до машины) получает эталон при первом пульсе с пометкой — дрифт для неё со второго дня.
"""
from __future__ import annotations

import json
import re
import time

from ..adapters.page_fetch import PageFetcher
from .errors import NetworkUnreachable, SourceError
from .htmldoc import HtmlDoc
from .snapshots import save_live
from .urls import norm_url, slug_of

CRITICAL, IMPORTANT, UNVERIFIED = "критично", "важно", "не проверено"
SLEEP = time.sleep


def live_snapshots(instance) -> dict[str, dict]:
    folder = instance.root / "data" / "snapshots" / "live"
    out = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        out[path.stem] = data
    return out


def picture(instance, doc: HtmlDoc, url: str, status: int) -> dict:
    """То же, что эталонный снимок «доехало» (Т22.6)."""
    return {"url": url, "date": instance.today.isoformat(), "status": status, "canonical": doc.canonical,
            "robots": doc.robots, "title": doc.title, "description": doc.description, "h1": doc.headings(1),
            "schema": sorted({str(n.get("@type")) for n in doc.schema_nodes()}), "og_image": doc.og_image,
            "text_chars": len(re.sub(r"\s+", "", doc.main_text()))}


def compare(instance, ref: dict, got: dict) -> list[dict]:
    """Находки дрифта: уровень, что, было → стало."""
    out = []

    def add(level, what, was, now):
        out.append({"level": level, "what": what, "was": was, "now": now})

    if got["status"] != 200:
        add(CRITICAL, "ответ сайта", ref.get("status", 200), got["status"])
        return out
    if "noindex" in (got.get("robots") or "").lower() and "noindex" not in (ref.get("robots") or "").lower():
        add(CRITICAL, "запрет индексации", ref.get("robots") or "—", got["robots"])
    if ref.get("canonical") and norm_url(got.get("canonical") or "") != norm_url(ref["canonical"]):
        add(CRITICAL, "canonical", ref["canonical"], got.get("canonical") or "нет")
    if ref.get("h1") and not got.get("h1"):
        add(CRITICAL, "H1 пропал", ref["h1"], [])
    if ref.get("schema") and not got.get("schema"):
        add(CRITICAL, "разметка не читается", ref["schema"], [])
    for name in ("title", "description"):
        if ref.get(name) and got.get(name) and got[name].strip() != ref[name].strip():
            add(IMPORTANT, f"переписан {name}", ref[name], got[name])
    if ref.get("h1") and got.get("h1") and got["h1"] != ref["h1"]:
        add(IMPORTANT, "переписан H1", ref["h1"], got["h1"])
    if ref.get("og_image") and not got.get("og_image"):
        add(IMPORTANT, "пропала картинка для соцсетей", ref["og_image"], "")
    was, now = ref.get("text_chars") or 0, got.get("text_chars") or 0
    if was and now < was * (1 - instance.p["monitor"]["drift_shrink"]):
        add(IMPORTANT, "текст ужался", was, now)
    return out


def check(instance, *, transport=None, extra_urls=()) -> dict:
    """Дрифт по всем эталонам; адресам из `extra_urls` без эталона — снимается эталон (база)."""
    fetcher = PageFetcher.for_own_site(instance, transport=transport if transport is not None else instance.transport)
    refs = live_snapshots(instance)
    pages, based, findings = 0, [], []
    delay = instance.p["audit"]["delay_seconds"]
    known = {norm_url(r["url"]) for r in refs.values()}
    todo = [(r["url"], r) for r in refs.values()] + [(u, None) for u in extra_urls if norm_url(u) not in known]
    for n, (url, ref) in enumerate(todo):
        if n and delay:
            SLEEP(delay)
        try:
            page = fetcher.get(url, allow_redirects=False)
        except NetworkUnreachable as exc:
            findings.append({"url": url, "level": UNVERIFIED, "what": "сеть не ответила", "was": "", "now": str(exc)})
            continue
        except SourceError as exc:
            findings.append({"url": url, "level": CRITICAL, "what": "ответ сайта", "was": 200, "now": str(exc)})
            continue
        pages += 1
        if page.status in (301, 302, 303, 307, 308):
            if ref is None:                    # эталона нет: страница давно переехала — старый адрес в корпусе, не поломка
                findings.append({"url": url, "level": IMPORTANT, "what": "адрес в корпусе устарел", "was": url,
                                 "now": page.location})
            else:                              # страница с эталоном (200) стала перенаправлять — это поломка
                findings.append({"url": url, "level": CRITICAL, "what": "перенаправление", "was": url,
                                 "now": page.location})
            continue
        doc = HtmlDoc(page.text, instance.p["prepare"]["main_blocks"])
        got = picture(instance, doc, url, page.status)
        if ref is None:
            if page.status != 200:
                findings.append({"url": url, "level": IMPORTANT, "what": "страница корпуса не отвечает 200",
                                 "was": "", "now": f"HTTP {page.status}"})
                continue
            if slug_of(url):
                save_live(instance, slug_of(url), {**got, "note": f"эталон снят пульсом {instance.today.isoformat()}"})
                based.append(url)
            continue
        findings += [{"url": url, **f} for f in compare(instance, ref, got)]
    return {"pages": pages, "baselined": based, "findings": findings,
            "critical": sum(1 for f in findings if f["level"] == CRITICAL)}
