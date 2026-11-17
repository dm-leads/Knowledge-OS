"""1.6.0, дожим (аудит 17.11.2026, правка 5): лидеров выдачи по Search Console не правим ни одной веткой; ветка возраста
не трогает статью со ставкой, у которой окно замера открыто."""
from datetime import timedelta

import pytest

from content_engine.core import store
from content_engine.core.errors import GateRefused
from content_engine.core.urls import norm_url
from content_engine.steps import refresh

from .conftest import TODAY
from .test_refresh import URL, inst, pos, write_positions


def test_leader_by_search_console_is_not_candidate(project):
    i = inst(project)
    write_positions(i, [pos("отопление теплицы", 2.0, 500), pos("теплица зимой", 8.0, 80)])
    out = refresh.scan(i)
    assert norm_url(URL) not in [c["url"] for c in out["candidates"]]
    assert any(x["kind"] == "leader" and "позиция 2.0" in x["why"] for x in out["no_data"])
    assert out["blocks"]["leaders"]["found"] == 1


def test_top_position_with_few_impressions_is_not_leader(project):
    i = inst(project)
    write_positions(i, [pos("отопление теплицы", 2.0, 2), pos("теплица зимой", 8.0, 80)])
    assert norm_url(URL) in [c["url"] for c in refresh.scan(i)["candidates"]]


def test_webmaster_row_does_not_make_leader(project):
    i = inst(project)
    write_positions(i, [{**pos("отопление теплицы", 2.0, 500), "engine": "yandex"}, pos("теплица зимой", 8.0, 80)])
    assert norm_url(URL) in [c["url"] for c in refresh.scan(i)["candidates"]]


def test_take_refuses_leader(project):
    i = inst(project)
    write_positions(i, [pos("отопление теплицы", 1.0, 900)])
    with pytest.raises(GateRefused, match="лидер выдачи"):
        refresh.take(i, URL)


def _bet(project, *, missing):
    i = inst(project)
    published = (TODAY - timedelta(days=150)).isoformat()
    store.write_json(i.file("bets"), "bets", [{"id": "T1", "url": "example.ru/blog/otoplenie-teplicy",
                                                 "published": published, "bet_missing": missing, "measures": {}}])
    return i


def test_age_skips_article_with_open_bet_window(project):
    out = refresh.scan(_bet(project, missing=False))
    aged = [c["url"] for c in out["candidates"] if any(r["kind"] == "age" for r in c["reasons"])]
    assert norm_url(URL) not in aged
    assert any("окно замера ставки открыто" in x["why"] for x in out["no_data"])


def test_age_takes_article_without_bet(project):
    out = refresh.scan(_bet(project, missing=True))
    aged = [c["url"] for c in out["candidates"] if any(r["kind"] == "age" for r in c["reasons"])]
    assert norm_url(URL) in aged
