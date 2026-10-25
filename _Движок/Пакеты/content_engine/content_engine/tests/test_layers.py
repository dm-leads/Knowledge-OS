"""Семь слоёв анти-дублей (Т10), белый список (Т10.1), тип выдачи (Т11.2), журнал отсева (Т10.2)."""
import pytest

from content_engine.adapters.fake import FakeSerp
from content_engine.core import store
from content_engine.core.instance import Instance
from content_engine.core.quota import fetch_serp
from content_engine.steps import dedup, reservations
from content_engine.steps.demand_map import build_map
from content_engine.steps.gate_topic import Candidate, gate, log_rejection

from .conftest import TODAY, set_profile
from .test_demand_map import setup

PAGE_TOP = ["a.example/1", "b.example/2", "c.example/3", "d.example/4", "e.example/5", "f.example/6",
            "g.example/7", "h.example/8", "i.example/9", "example.ru/blog/otoplenie-teplicy"]
SERP = {"remaining": 1000, "top": {"msk": {
    "отопление теплицы": [[u, i + 1] for i, u in enumerate(PAGE_TOP)],
    "обогрев теплицы": [["a.example/1", 1], ["b.example/2", 2], ["c.example/3", 3], ["z.example/1", 4]],
    "чем греть теплицу": [["z.example/2", 1]],
    "теплица обогрев": [["z.example/3", 1]],
    "обрезка малины осенью": [["berry.example/1", 1], ["example.ru/blog/gazon-vesnoj", 4]],
    "как обрезать малину": [["berry.example/2", 1]],
    "когда обрезать малину": [["berry.example/3", 1]],
    "купить теплицу": [["market.example/t", 1], ["shop.example/t", 2], ["z.example/9", 3]],
    "теплица цена": [["z.example/8", 1]],
    "теплица недорого": [["z.example/7", 1]],
}}}


@pytest.fixture
def ctx_factory(project):
    instance = setup(project)
    serp = FakeSerp(SERP)
    mapped = Instance(project, today=TODAY, serp=serp)
    build_map(mapped)                                       # головные запросы страниц попадают в кэш выдачи

    def make(**kwargs):
        inst = Instance(project, today=TODAY, serp=serp)
        return dedup.Context(inst, **kwargs)
    return make


def run(ctx, topic, formulations, number, **kw):
    cand = Candidate(topic, formulations, **kw)
    return dedup.LAYERS[number](ctx, cand, ctx.whitelisted(cand))


FREE = ("обрезка малины осенью", ["обрезка малины осенью", "как обрезать малину", "когда обрезать малину"])


def test_layer1_key_against_written(ctx_factory):
    ctx = ctx_factory(offline=True)
    hit = run(ctx, "отопление теплицы зимой", ["a", "b", "c"], 1)
    assert hit.outcome == dedup.BLOCK and hit.page == "example.ru/blog/otoplenie-teplicy"
    assert run(ctx, *FREE, 1).outcome == dedup.PASS


def test_layer1_reservation(ctx_factory):
    ctx = ctx_factory(offline=True)
    reservations.create(ctx.instance, topic="малина", key=[], cluster_id="C4", who="тест")
    hit = run(ctx, *FREE, 1, cluster_id="C4")
    assert hit.outcome == dedup.BLOCK and "забронирован" in hit.reason


def test_layer3_common_serp(ctx_factory):
    ctx = ctx_factory()
    hit = run(ctx, "обогрев теплицы", ["обогрев теплицы", "чем греть теплицу", "теплица обогрев"], 3)
    assert hit.outcome == dedup.BLOCK and hit.numbers["common"] == 3
    assert hit.page in {"example.ru/blog/otoplenie-teplicy", "example.ru/blog/gazon-vesnoj"}
    assert run(ctx, *FREE, 3).outcome == dedup.PASS


def test_layer3_offline_is_skipped(ctx_factory):
    assert run(ctx_factory(offline=True), *FREE, 3).outcome == dedup.SKIPPED


def test_layer4_value_ladder(ctx_factory, tmp_path):
    ctx = ctx_factory()
    warn = run(ctx, *FREE, 4)                               # наша страница в топ-10, данных о деньгах нет
    assert warn.outcome == dedup.WARN and "не блокируют" in warn.reason
    money = tmp_path / "money.csv"
    money.write_text("slug;money;leads;period_from;period_to;source;as_of\n"
                     "gazon-vesnoj;1200;1;2025-09-01;2026-02-28;crm;2026-03-01\n", encoding="utf-8")
    set_profile(ctx.instance.profile_path, lambda d: d["sources"]["value"].update(kind="file", file=str(money)))
    ctx2 = ctx_factory()
    ctx2._map = {"records": [], "clusters": []}             # карта без денег — значение берётся из файла
    block = run(ctx2, *FREE, 4)
    assert block.outcome == dedup.BLOCK and "зарабатывает" in block.reason


def test_layer5_sales_owner(ctx_factory):
    ctx = ctx_factory(offline=True)
    hit = run(ctx, "обогреватель для теплицы", ["a", "b", "c"], 5, cluster_id="C2")
    assert hit.outcome == dedup.BLOCK and hit.page == "example.ru/catalog/obogrevateli"
    assert run(ctx, *FREE, 5).outcome == dedup.PASS


def test_layer6_head_term_cap(ctx_factory):
    ctx = ctx_factory(offline=True)
    assert run(ctx, "газон", ["a", "b", "c"], 6).outcome == dedup.PASS
    ctx.instance.profile["thresholds"]["head_term_cap"] = 1
    assert run(ctx, "газон", ["a", "b", "c"], 6).outcome == dedup.BLOCK


def test_layer7_title_near_duplicate(ctx_factory):
    ctx = ctx_factory(offline=True)
    hit = run(ctx, "Укрывать розы на зиму", ["a", "b", "c"], 7)
    assert hit.outcome == dedup.BLOCK and hit.page == "example.ru/blog/ukrytie-roz"
    assert run(ctx, "розы", ["a", "b", "c"], 7).outcome == dedup.PASS    # короткое внутри длинного — не дубль


def test_whitelist_silences_page_on_every_layer(ctx_factory):
    ctx = ctx_factory(offline=True)
    store.write_json(ctx.instance.file("whitelist"), "whitelist", [
        {"topic": "Укрывать розы на зиму", "page": "ukrytie-roz", "reason": "другой угол", "who": "редактор",
         "date": TODAY.isoformat()}])
    assert run(ctx, "Укрывать розы на зиму", ["a", "b", "c"], 7).outcome == dedup.PASS
    assert run(ctx, "Укрывать розы на зиму", ["a", "b", "c"], 1).page != "example.ru/blog/ukrytie-roz"


def test_commercial_serp_and_journal(ctx_factory):
    ctx = ctx_factory()
    ctx.instance.profile["thresholds"]["commercial_min"] = 2
    report = gate(ctx.instance, Candidate("купить теплицу", ["купить теплицу", "теплица цена", "теплица недорого"]))
    assert report["verdict"] == "коммерческая"
    assert "дописано 1 — прочитано 1" in log_rejection(ctx.instance, report)
    assert store.read_jsonl(ctx.instance.file("rejections"))[0]["verdict"] == "коммерческая"


def test_full_gate_free_topic(ctx_factory):
    ctx = ctx_factory()
    cand = Candidate("посадка чеснока под зиму", ["посадка чеснока под зиму", "когда сажать чеснок",
                                                 "как сажать чеснок осенью"],
                     bet={"hypothesis": "h", "expectation": "e", "fail_threshold": "f"})
    report = gate(ctx.instance, cand)
    assert report["verdict"] == "свободно" and report["complete"]


def test_quota_shortage_makes_gate_incomplete(project):
    instance = setup(project)
    serp = FakeSerp({"remaining": 0, "top": {"msk": {}}})
    inst = Instance(project, today=TODAY, serp=serp)
    report = gate(inst, Candidate(*FREE))
    assert not report["complete"]
    assert fetch_serp(inst, ["x"])[1].left_for_tomorrow == 1
