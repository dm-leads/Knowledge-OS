"""Книга ставок с деньгами (модуль Д2, Б1, Т39, П58): задним числом, замер от дня выкладки, итог — человеком,
предложение в формате Движка роста без записи в Движок; решение Движка по направлению — в очереди."""
import hashlib
from datetime import date, timedelta

import pytest
import yaml

from content_engine.cli import main
from content_engine.core import bets, locks, store
from content_engine.core.errors import GateRefused, LockBusy
from content_engine.core.instance import Instance
from content_engine.steps import bets_cmd
from content_engine.steps import queue as queue_mod

from .conftest import TODAY, set_profile, write_article

URL = "https://example.ru/blog/otoplenie-teplicy"


def record(rid="T0001", published="2025-10-01", **extra):
    first = date.fromisoformat(published)
    base = {"id": rid, "topic": "отопление теплицы", "url": "example.ru/blog/otoplenie-teplicy",
            "hypothesis": "h", "hypothesis_id": "H-1", "expectation": "4 заявки",
            "fail_threshold": "ноль заявок за окно", "published": published, "built": "2025-09-01",
            "measure": {str(w): (first + timedelta(days=w)).isoformat() for w in (90, 180)},
            "result": None, "bet_missing": False, "direction": "отопление",
            "expectation_numbers": [{"value": 4, "unit": "заявки", "cabinet": "", "period": "180 дней",
                                     "denominator": "", "source": "H-1", "as_of": "2025-09-01", "status": "оценка"}],
            "unit": "article", "planned_count": 1}
    base.update(extra)
    return base


def money_file(project, rows):
    path = project.parent / "money.csv"
    path.write_text("slug;money;leads;period_from;period_to;source;as_of\n" + "".join(
        f"{s};{m};{l};{f};{t};roistat;2026-03-01\n" for s, m, l, f, t in rows), encoding="utf-8")
    set_profile(project, lambda d: d["bets"]["money"].update(kind="file", file="money.csv"))


def window(published, days):
    first = date.fromisoformat(published)
    return first.isoformat(), (first + timedelta(days=days - 1)).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


@pytest.fixture
def corpus(project):
    folder = project.parent / "texts"
    write_article(folder, "otoplenie-teplicy", "Отопление теплицы", "## Печь\n\nТекст про печь.")
    path = folder / "otoplenie-teplicy.md"
    text = path.read_text(encoding="utf-8").replace("---\n", "---\ndate: 2025-10-01\n", 1)
    path.write_text(text, encoding="utf-8")
    write_article(folder, "gazon-vesnoj", "Газон весной", "## Посев\n\nТекст про газон.")
    set_profile(project, lambda d: d["corpus"]["folders"][0]["fields"].update(date="date"))
    return folder


def test_backfill_plan_writes_nothing_and_repeat_adds_nothing(project, corpus):
    instance = Instance(project, today=TODAY)
    path = instance.file("bets")
    before = digest(path)
    out = bets_cmd.backfill(instance, apply=False)
    assert out["new"] == out["pages"] >= 2 and digest(path) == before
    bets_cmd.backfill(instance, apply=True)
    assert len(bets.load(instance)) == out["new"]
    assert bets_cmd.backfill(Instance(project, today=TODAY), apply=True)["new"] == 0


def test_backfill_day_sources(project, corpus):
    instance = Instance(project, today=TODAY)
    out = bets_cmd.backfill(instance, apply=True)
    assert out["sources"]["из шапки статьи"] == 1 and out["sources"]["восстановлен: gsc"] == 0
    assert out["sources"]["не установлен"] == out["new"] - 1           # Search Console выключен
    recs = {r["id"]: r for r in bets.load(instance)}
    assert recs["C:otoplenie-teplicy"]["published"] == "2025-10-01" and recs["C:otoplenie-teplicy"]["bet_missing"]
    assert recs["C:gazon-vesnoj"]["published"] is None and "measure" not in recs["C:gazon-vesnoj"]


def test_machine_article_gets_no_second_record(project, corpus):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record()])
    out = bets_cmd.backfill(instance, apply=True)
    ids = [r["id"] for r in bets.load(instance)]
    assert ids[0] == "T0001" and "C:otoplenie-teplicy" not in ids and "C:gazon-vesnoj" in ids
    assert out["new"] == out["pages"] - 1


def test_measure_from_publication_day_not_build(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record()])
    f, t = window("2025-10-01", 90)
    money_file(project, [("otoplenie-teplicy", 30000, 5, f, t)])
    bets_cmd.measure(Instance(project, today=TODAY))
    m = bets.load(instance)[0]["measures"]["90"]
    assert m["money"]["period_from"] == "2025-10-01" and m["proposed"] == "сыграла"   # 5 ≥ 4 заявок
    assert m["result"] is None                                           # итог ставит человек


def test_proxy_is_low_data(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record()])
    store.write_json(instance.file("demand_map"), "demand_map", [{"id": "otoplenie-teplicy", "url": URL,
        "value": {"step": 3, "money": {"value": 900.0}}}], meta={"clusters": [], "candidates": [], "report": {}})
    money_file(project, [])
    bets_cmd.measure(Instance(project, today=TODAY))
    m = bets.load(instance)[0]["measures"]["90"]
    assert m["step"] == 3 and m["proposed"] == "мало данных"


def test_window_not_yet_with_date(project):
    instance = Instance(project, today=TODAY)
    fresh = (TODAY - timedelta(days=10)).isoformat()
    store.write_json(instance.file("bets"), "bets", [record(published=fresh)])
    out = bets_cmd.measure(instance)
    assert out["counts"]["окно не наступило"] == 2 and "measures" not in bets.load(instance)[0] or \
        bets.load(instance)[0]["measures"] == {}
    assert out["upcoming"][0][0] == (TODAY + timedelta(days=80)).isoformat()


def test_missing_row_is_no_data_not_failure(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record()])
    money_file(project, [("drugaya-stranica", 0, 0, *window("2025-10-01", 90))])
    bets_cmd.measure(Instance(project, today=TODAY))
    m = bets.load(instance)[0]["measures"]["90"]
    assert m["money"]["status"] == "нет данных" and m["money"]["value"] is None and m["proposed"] == "мало данных"


def test_between_threshold_and_expectation_is_low_data(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record(fail_threshold="1 заявка")])
    money_file(project, [("otoplenie-teplicy", 9000, 2, *window("2025-10-01", 90))])
    bets_cmd.measure(Instance(project, today=TODAY))
    m = bets.load(instance)[0]["measures"]["90"]
    assert m["proposed"] == "мало данных" and "между порогом" in m["why"]


def test_no_bet_measures_money(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record(bet_missing=True)])
    money_file(project, [("otoplenie-teplicy", 12000, 3, *window("2025-10-01", 90))])
    bets_cmd.measure(Instance(project, today=TODAY))
    m = bets.load(instance)[0]["measures"]["90"]
    assert m["proposed"] == "ставки нет" and m["money"]["value"] == 12000


def test_write_under_lock_refuses_parallel(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record()])
    with locks.held(instance, "bets", "write"):
        with pytest.raises(LockBusy):
            bets_cmd.measure(Instance(project, today=TODAY))
    assert "measures" not in bets.load(instance)[0]


def test_result_needs_human_before_proposal(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record()])
    money_file(project, [("otoplenie-teplicy", 30000, 5, *window("2025-10-01", 90))])
    bets_cmd.measure(Instance(project, today=TODAY))
    with pytest.raises(GateRefused):
        bets_cmd.propose(Instance(project, today=TODAY))                # итог не принят — предложения нет
    with pytest.raises(Exception, match="--reason"):
        bets_cmd.accept(Instance(project, today=TODAY), "T0001", "90", "Дмитрий", "провал", None)
    bets_cmd.accept(Instance(project, today=TODAY), "T0001", "90", "Дмитрий", None, None)
    assert bets.load(instance)[0]["measures"]["90"]["result"] == "сыграла"


def test_proposal_in_engine_format_no_engine_calls(project):
    calls = []
    instance = Instance(project, today=TODAY, transport=lambda *a, **k: calls.append(a))
    store.write_json(instance.file("bets"), "bets", [record()])
    money_file(project, [("otoplenie-teplicy", 30000, 5, *window("2025-10-01", 90))])
    bets_cmd.measure(Instance(project, today=TODAY))
    bets_cmd.accept(Instance(project, today=TODAY), "T0001", "90", "Дмитрий", None, None)
    out = bets_cmd.propose(Instance(project, today=TODAY, transport=lambda *a, **k: calls.append(a)))
    data = yaml.safe_load(open(out["files"][0], encoding="utf-8"))
    row = data["numbers"][0]
    assert set(row) == {"metric", "level", "scope", "flow", "segment", "period_start", "period_end", "as_of",
                        "source", "status", "value", "denominator", "unit", "missing"}
    assert row["unit"] == "заявки" and row["value"] == 5 and data["hypothesis_id"] == "H-1"
    assert calls == []                                                   # ни одного запроса к Движку
    with pytest.raises(GateRefused):
        bets_cmd.propose(Instance(project, today=TODAY))                 # повтор — уже предложено


def card(project, hid, decision, exported=None):
    folder = project.parent / "growth"
    folder.mkdir(exist_ok=True)
    from content_engine.adapters.growth import card_template
    data = yaml.safe_load(card_template())
    data.update(id=hid, decision=decision, decided_at="2026-02-20",
                exported_at=(exported or TODAY).isoformat())
    (folder / f"{hid}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    set_profile(project, lambda d: d["sources"]["growth"].update(kind="file", path="growth"))


def test_kill_closes_direction_and_scale_raises_weight(project):
    set_profile(project, lambda d: (d["directions"][0].update(hypothesis_id="H-7"),
                                    d["directions"][1].update(hypothesis_id="H-8")))
    card(project, "H-7", "kill")
    card(project, "H-8", "scale")
    instance = Instance(project, today=TODAY)
    names = [d["name"] for d in instance.p["directions"]]
    assert queue_mod.closed_direction(instance, names[0]).startswith("направление закрыто данными: H-7")
    assert queue_mod.closed_direction(instance, names[1]) == ""
    w0 = instance.p["directions"][1]["weight"]
    key = queue_mod.priority_key(instance, {"id": "T1", "direction": names[1], "formulations": ["а"], "weight": 1})
    assert key[0] == -(w0 + 1)


def test_stale_card_decision_not_applied(project):
    set_profile(project, lambda d: d["directions"][0].update(hypothesis_id="H-7"))
    card(project, "H-7", "kill", exported=TODAY - timedelta(days=40))
    instance = Instance(project, today=TODAY)
    assert queue_mod.closed_direction(instance, instance.p["directions"][0]["name"]) == ""
    assert "старше" in queue_mod.direction_decisions(instance)[instance.p["directions"][0]["name"]]["note"]


def test_old_card_without_decision_reads(project):
    from content_engine.adapters.growth import FileGrowth
    card(project, "H-9", "")
    path = project.parent / "growth" / "H-9.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.pop("decision"), data.pop("decided_at")
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert FileGrowth(project.parent / "growth", TODAY).decision("H-9")["decision"] == ""


def test_bet_commands_via_main(project, corpus, capsys):
    assert main(["bet", "backfill", "--profile", str(project)]) == 0
    assert "план" in capsys.readouterr().out
