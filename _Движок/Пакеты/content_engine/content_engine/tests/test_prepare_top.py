"""Снимок выдачи темы, перевзятие, разбор топа и доноры (Т18, П23–П25).

Страницы конкурентов собираются в тесте из явных строк: так ожидаемые числа (знаки, картинки, таблицы, пробелы)
считаются рядом с текстом страницы, а не подбираются под результат.
"""
import json
import math
import re
from datetime import timedelta

import pytest

from content_engine.adapters.fake import FakeSerp
from content_engine.adapters.page_fetch import PageFetcher
from content_engine.core import store
from content_engine.core.errors import EndpointNotAllowed, GateRefused
from content_engine.core.instance import Instance
from content_engine.core.serp_cache import SerpCache
from content_engine.adapters.serp import OK, SerpResult
from content_engine.steps import prepare
from content_engine.steps import queue as queue_mod
from content_engine.steps import reservations

from .conftest import TODAY, set_profile
from .site_fixture import Page

TID = "T0001"
FORMULATIONS = ["укрытие роз", "укрытие роз лапником", "материалы для укрытия роз"]   # основы есть в статье корпуса о розах


def page(h1, sections, *, tables=0, images=0, chrome=True):
    """HTML страницы и число знаков основного текста без пробелов."""
    parts, texts = [f"<h1>{h1}</h1>"], [h1]
    for h2, paragraph in sections:
        parts += [f"<h2>{h2}</h2>", f"<p>{paragraph}</p>"]
        texts += [h2, paragraph]
    for n in range(tables):
        parts.append(f"<table><tr><td>Строка {n}</td><td>Значение</td></tr></table>")
        texts += [f"Строка {n}", "Значение"]
    parts += [f'<img src="/i{n}.jpg" alt="картинка">' for n in range(images)]
    menu = "<nav>Меню сайта Главная Каталог</nav><footer>Подвал сайта</footer>" if chrome else ""
    html = f"<html><head><title>{h1}</title></head><body>{menu}<article>{''.join(parts)}</article></body></html>"
    return html, sum(len(re.sub(r"\s", "", t)) for t in texts)


C1 = page("Как укрыть розы", [("Материалы для укрытия роз", "Лапник и спанбонд защищают кусты от мороза."),
                              ("Когда снимать укрытие весной", "Снимают постепенно, когда уйдут ночные заморозки.")],
          tables=1, images=2)
C2 = page("Розы зимой", [("Обрезка перед укрытием", "Побеги укорачивают до сорока сантиметров."),
                         ("Окучивание кустов", "Землю подсыпают к основанию куста горкой.")], images=4)
C3 = page("Укрытие роз", [("Каркас из дуг", "Дуги ставят над кустом и натягивают ткань.")], images=1)
EMPTY = ("<html><body><script>document.write('текст из скрипта')</script></body></html>", 0)

URLS = {"c1.example/roses": C1, "c2.example/roses": C2, "c3.example/roses": C3, "c4.example/empty": EMPTY}
TOP = [["example.ru/blog/ukrytie-roz", 1], ["market.example/rozy", 2], ["c4.example/empty", 3],
       ["c1.example/roses", 4], ["z.example", 5], ["c2.example/catalog/rozy", 6], ["c2.example/roses", 7],
       ["c3.example/roses", 8]]


class Web:
    """Подмена HTTP: адрес → страница; журнал запросов."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.log = []

    def __call__(self, method, url, **kwargs):
        self.log.append(url)
        from content_engine.core.urls import norm_url
        hit = self.pages.get(norm_url(url))
        return Page(200, hit[0]) if hit else Page(404, "нет")


@pytest.fixture
def web():
    return Web(dict(URLS))


def taken_topic(project, web, *, as_of=TODAY, top=TOP, direction="розы"):
    set_profile(project, lambda d: d["directions"].append({"name": "розы", "words": ["роза"], "weight": 1,
                                                           "quarter_quota": 0, "volume": [100, 50000]}))
    inst = Instance(project, today=TODAY, transport=web, serp=FakeSerp({"remaining": 100, "top": {}}))
    queue_mod.save(inst, [{"id": TID, "topic": FORMULATIONS[0], "formulations": FORMULATIONS, "cluster_id": None,
                           "direction": direction, "weight": 10, "manual_priority": 0, "status": queue_mod.TAKEN,
                           "gate": None, "reservation_id": None, "bet": {}, "created": TODAY.isoformat(),
                           "rejected": None, "prefilter": None, "taken": TODAY.isoformat()}])
    records = [{"phrase": f, "base": "msk", "status": OK, "top": top if f == FORMULATIONS[0] else []}
               for f in FORMULATIONS]
    store.write_json(inst.brief_dir(TID) / "serp.json", "topic_serp", records,
                     meta={"id": TID, "as_of": as_of.isoformat(), "formulations": FORMULATIONS})
    return inst


def test_top_numbers_selection_and_no_serp_calls(project, web):
    inst = taken_topic(project, web)
    serp = inst.serp()
    out = prepare.top(inst, TID)
    assert serp.requested == [] and serp.remaining() == 100 and out["serp_requests"] == 0
    assert [p["url"] for p in out["pages"]] == ["c1.example/roses", "c2.example/roses", "c3.example/roses"]
    dropped = {d["url"]: d["why"] for d in out["dropped"]}
    assert dropped["example.ru/blog/ukrytie-roz"] == "свой домен"
    assert dropped["market.example/rozy"] == "коммерческий домен"
    assert "не статья" in dropped["z.example"] and "не статья" in dropped["c2.example/catalog/rozy"]
    empty = next(s for s in out["skipped"] if s["url"] == "c4.example/empty")
    assert "не разобрано" in empty["why"]
    chars = [C1[1], C2[1], C3[1]]
    assert [p["chars"] for p in out["pages"]] == chars
    n = out["numbers"]
    assert n["volume_chars"]["value"] == round(sum(chars) / 3) and n["volume_chars"]["status"] == "факт"
    assert n["images"]["value"] == math.ceil(4 * 1.15) == 5
    assert n["table"]["need"] is True and "c1.example/roses" in n["table"]["sources"]
    assert out["facts"]["status"] == "не подключено" and out["complete"]
    gaps = {g["heading"] for g in out["gaps"]}
    assert "Когда снимать укрытие весной" in gaps and "Материалы для укрытия роз" not in gaps
    assert "ukrytie-roz" in out["our_pages"]
    back = store.read_json(inst.brief_dir(TID) / "top.json", "topic_top")
    assert len(back["records"]) == 3 and (inst.brief_dir(TID) / "top.md").is_file()


def test_volume_max_and_corridor(project, web):
    inst = taken_topic(project, web)
    set_profile(project, lambda d: d["prepare"].update(volume_rule="max", table_rule="all"))
    inst = Instance(project, today=TODAY, transport=web)
    out = prepare.top(inst, TID)
    assert out["numbers"]["volume_chars"]["value"] == max(C1[1], C2[1], C3[1])
    assert out["numbers"]["table"]["need"] is False
    set_profile(project, lambda d: d["directions"][-1].update(volume=[6000, 12000]))
    out = prepare.top(Instance(project, today=TODAY, transport=web), TID)
    assert out["numbers"]["volume_chars"]["value"] == 6000 and "вне коридора" in out["numbers"]["volume_chars"]["note"]


def test_not_taken_and_old_snapshot_refused(project, web):
    inst = taken_topic(project, web, as_of=TODAY - timedelta(days=4))
    with pytest.raises(GateRefused, match="queue retake"):
        prepare.top(inst, TID)
    topics = queue_mod.load(inst)
    topics[0]["status"] = queue_mod.GATED
    queue_mod.save(inst, topics)
    with pytest.raises(GateRefused, match="только у темы taken"):
        prepare.top(inst, TID)


def test_incomplete_when_not_enough_pages(project, web):
    inst = taken_topic(project, web, top=[["c1.example/roses", 1], ["c4.example/empty", 2]])
    out = prepare.top(inst, TID)
    assert not out["complete"] and "разобрано 1 из 3" in out["incomplete_reason"]


def test_fetcher_refuses_url_outside_snapshot(web):
    fetcher = PageFetcher(allowed_urls=["c1.example/roses"], transport=web)
    with pytest.raises(EndpointNotAllowed):
        fetcher.get("https://c2.example/roses")
    assert web.log == []


def test_snapshot_survives_cache_overwrite(project, web):
    inst = taken_topic(project, web)
    cache = SerpCache(inst.root, 45, TODAY)
    cache.put(SerpResult(FORMULATIONS[0], "msk", OK, [("other.example/x", 1)]))
    snap = store.read_json(inst.brief_dir(TID) / "serp.json", "topic_serp")
    assert snap["records"][0]["top"][0][0] == "example.ru/blog/ukrytie-roz"
    out = prepare.top(inst, TID)
    assert "other.example/x" not in [p["url"] for p in out["pages"]]


def mapped_topic(project, status):
    """Тема «обрезка малины осенью» с картой спроса, бронью и отчётом гейта «свободно» на сегодня."""
    from .test_demand_map import setup
    from .test_layers import SERP
    from .test_queue import topic
    from content_engine.steps.demand_map import build_map
    setup(project)
    serp = FakeSerp(SERP)
    build_map(Instance(project, today=TODAY, serp=serp))
    inst = Instance(project, today=TODAY, serp=serp)
    formulations = ["обрезка малины осенью", "как обрезать малину", "когда обрезать малину"]
    r = reservations.create(inst, topic=formulations[0], key=[], cluster_id="C4", who="тест", queue_id=TID)
    gate_path = inst.reports_dir("gates") / f"{TID}.json"
    gate_path.write_text(json.dumps({"queue_id": TID, "topic": formulations[0], "complete": True,
                                     "verdict": "свободно", "date": TODAY.isoformat()}), encoding="utf-8")
    queue_mod.save(inst, [topic(TID, topic=formulations[0], formulations=formulations, status=status,
                                cluster_id="C4", reservation_id=r["id"],
                                gate={"report": str(gate_path), "date": TODAY.isoformat(), "verdict": "свободно",
                                      "complete": True})])
    return inst, serp


def test_take_writes_snapshot_unchanged_by_cache(project):
    inst, serp = mapped_topic(project, queue_mod.GATED)
    assert queue_mod.take(inst, TID)["taken"]
    path = inst.brief_dir(TID) / "serp.json"
    before = path.read_text(encoding="utf-8")
    snap = store.read_json(path, "topic_serp")
    assert snap["as_of"] == TODAY.isoformat() and len(snap["records"]) == 3
    SerpCache(inst.root, 45, TODAY).put(SerpResult("обрезка малины осенью", "msk", OK, [("other.example/x", 1)]))
    assert path.read_text(encoding="utf-8") == before


def test_retake_with_live_reservation_and_refused_when_expired(project):
    inst, serp = mapped_topic(project, queue_mod.TAKEN)
    later = Instance(project, today=TODAY + timedelta(days=2), serp=serp)
    out = queue_mod.retake(later, TID)
    snap = store.read_json(later.brief_dir(TID) / "serp.json", "topic_serp")
    assert out["taken"] and snap["as_of"] == (TODAY + timedelta(days=2)).isoformat()
    assert queue_mod.load(later)[0]["status"] == queue_mod.TAKEN
    records = reservations.load(later)
    records[0]["status"] = reservations.EXPIRED
    reservations.save(later, records)
    with pytest.raises(GateRefused, match="полный гейт заново"):
        queue_mod.retake(later, TID)


DONOR_SITEMAP = """<urlset><url><loc>https://garden.example/rose-winter-protection</loc></url>
<url><loc>https://garden.example/how-to-prune-roses</loc></url><url><loc>https://garden.example/lawn-care</loc></url>
</urlset>"""
DONOR_PAGE = ("<html><body><article><h2>Winter protection basics</h2><p>Cover roses when frost reaches 5 degrees "
              "below zero for three nights.</p><p>Mulch keeps roots warm and dry.</p></article></body></html>")


def test_donors_from_sitemap_theses_only_and_foreign_refused(project, web):
    inst = taken_topic(project, web)
    set_profile(project, lambda d: d["prepare"].update(donors=["garden.example"]))
    web.pages["garden.example/sitemap.xml"] = (DONOR_SITEMAP, 0)
    web.pages["garden.example/rose-winter-protection"] = (DONOR_PAGE, 0)
    inst = Instance(project, today=TODAY, transport=web)
    out = prepare.donors(inst, TID, ["protect roses in winter", "rose winter cover"], [])
    urls = [r["url"] for r in out["records"]]
    assert urls[0] == "garden.example/rose-winter-protection" and "garden.example/how-to-prune-roses" in urls
    assert "garden.example/lawn-care" not in urls
    with pytest.raises(GateRefused, match="не с домена"):
        prepare.donors(inst, TID, [], ["https://other.example/rose"])
    top = prepare.top(inst, TID)
    donor = next(d for d in top["donors"] if d["url"] == "garden.example/rose-winter-protection")
    kinds = {t["kind"] for t in donor["theses"]}
    assert kinds == {"заголовок", "число"} and all(t["source"] for t in donor["theses"])
    assert not any("Mulch keeps roots" in t["text"] for t in donor["theses"])   # текст без чисел не берётся
    assert "text" not in donor
