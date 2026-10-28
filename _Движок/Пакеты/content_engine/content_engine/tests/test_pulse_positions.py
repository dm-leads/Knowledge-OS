"""Позиции своих ключей и дрифт (модуль Д1, Т34.1–Т34.2, П50): тревоги по правилам профиля, «не подключено» вместо
нуля, «не проверено» при сетевом сбое."""
import json

import pytest
import requests

from content_engine.core import drift, positions, store
from content_engine.core.instance import Instance
from content_engine.core.snapshots import save_live

from .conftest import TODAY, set_profile
from .keysso_transport import Response
from .site_fixture import Page

URL = "https://example.ru/blog/otoplenie-teplicy"


def row(engine, query, pos, url=URL, date=TODAY.isoformat()):
    return {"date": date, "engine": engine, "query": query, "url": url, "position": pos}


@pytest.mark.parametrize("was, now, kinds", [
    (3, 8, {"падение"}),                         # на 5 — тревога
    (3, 7, set()),                               # на 4 — нет
    (12, 9, {"вход в топ-10"}),
    (9, 12, {"выход из топ-10"}),
    (28, 35, {"падение", "выход из топ-30"}),
])
def test_alarms_by_profile(project, was, now, kinds):
    instance = Instance(project, today=TODAY)
    got = positions.alarms(instance, [row("google", "отопление теплицы", now)],
                           [row("google", "отопление теплицы", was, date="2026-02-28")])
    assert {a["kind"] for a in got} == kinds


def _map(instance, keys):
    store.write_json(instance.file("demand_map"), "demand_map",
                     [{"id": "otoplenie-teplicy", "url": URL, "type": "article", "target_keys": keys}],
                     meta={"clusters": [], "candidates": [], "report": {}})


class Consoles:
    def __init__(self, gsc_status=200):
        self.gsc_status = gsc_status

    def __call__(self, method, url, params=None, json=None, headers=None, timeout=None, **kw):
        if "searchconsole" in url or "webmasters" in url:
            if self.gsc_status != 200:
                return Response(self.gsc_status, {})
            return Response(200, {"rows": [
                {"keys": [URL, "отопление теплицы"], "impressions": 40, "clicks": 3, "position": 4.2},
                {"keys": ["https://example.ru/blog/drugaya", "отопление теплицы"], "impressions": 5, "position": 30},
                {"keys": [URL, "чужой запрос"], "impressions": 9, "position": 2}]})
        if "webmaster.yandex" in url:
            return Response(200, {"queries": [{"query_text": "отопление теплицы",
                                               "indicators": {"TOTAL_SHOWS": 70, "AVG_SHOW_POSITION": 6.0}}]})
        return Response(404, {})


@pytest.fixture
def consoles(project, monkeypatch):
    monkeypatch.setenv("YANDEX_WEBMASTER_TOKEN", "x")
    import content_engine.adapters.gsc as gsc
    monkeypatch.setattr(gsc, "service_account_token", lambda key_file: "token")
    set_profile(project, lambda d: (d["sources"]["gsc"].update(enabled=True, site_url="sc-domain:example.ru",
                                                                  key_file="key.json"),
                                    d["sources"]["webmaster"].update(enabled=True, user_id="1", host_id="h",
                                                                        token_env="YANDEX_WEBMASTER_TOKEN")))


def test_snapshot_takes_own_keys_from_both_engines(project, consoles):
    instance = Instance(project, today=TODAY)
    _map(instance, ["отопление теплицы"])
    snap = positions.snapshot(instance, transport=Consoles())
    by = {(r["engine"], r["query"]): r for r in snap["rows"]}
    assert by[("google", "отопление теплицы")]["position"] == 4.2           # своя страница, чужая — отброшена
    assert by[("yandex", "отопление теплицы")]["position"] == 6.0
    assert ("google", "чужой запрос") not in by
    assert snap["engines"]["google"]["status"] == "ок" and all(r["status"] == "факт" for r in snap["rows"])


def test_failed_source_is_not_zero_and_does_not_stop_other(project, consoles):
    instance = Instance(project, today=TODAY)
    _map(instance, ["отопление теплицы"])
    snap = positions.snapshot(instance, transport=Consoles(gsc_status=500))
    assert snap["engines"]["google"]["status"] == "источник не ответил"
    assert [r["engine"] for r in snap["rows"]] == ["yandex"]


def test_disabled_consoles_are_not_connected(project):
    instance = Instance(project, today=TODAY)
    _map(instance, ["отопление теплицы"])
    snap = positions.snapshot(instance, transport=Consoles())
    assert snap["engines"]["google"]["status"] == "не подключено" and snap["rows"] == []


def page(canonical=URL, text="слово " * 300, title="Отопление теплицы", robots="", og="https://example.ru/og.jpg"):
    meta = f'<meta name="robots" content="{robots}">' if robots else ""
    return (f'<html><head><title>{title}</title><link rel="canonical" href="{canonical}">{meta}'
            f'<meta property="og:image" content="{og}">'
            '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article"}</script>'
            f"</head><body><article><h1>Отопление теплицы</h1><p>{text}</p></article></body></html>")


class Site:
    def __init__(self, pages):
        self.pages = pages

    def __call__(self, method, url, **kw):
        got = self.pages.get(url.rstrip("/"))
        if isinstance(got, Exception):
            raise got
        return got or Page(404, "нет")


def _baseline(project):
    instance = Instance(project, today=TODAY)
    drift.check(instance, transport=Site({URL: Page(200, page())}), extra_urls=[URL])
    return instance


@pytest.mark.parametrize("html, what, level", [
    (page(canonical="https://example.ru/blog/drugaya"), "canonical", "критично"),
    (page(robots="noindex"), "запрет индексации", "критично"),
    (page(text="слово " * 180), "текст ужался", "важно"),          # на 40 %
    (page(title="Новый заголовок"), "переписан title", "важно"),
])
def test_drift_levels(project, html, what, level):
    instance = _baseline(project)
    out = drift.check(instance, transport=Site({URL: Page(200, html)}))
    assert any(f["what"] == what and f["level"] == level for f in out["findings"]), out["findings"]


def test_small_shrink_is_not_drift(project):
    instance = _baseline(project)
    out = drift.check(instance, transport=Site({URL: Page(200, page(text="слово " * 250))}))   # на 17 %
    assert out["findings"] == []


def test_baseline_taken_once_and_network_is_unverified(project, monkeypatch):
    monkeypatch.setattr(drift, "SLEEP", lambda s: None)
    instance = _baseline(project)
    snap = json.loads((project.parent / "data" / "snapshots" / "live" / "otoplenie-teplicy.json").read_text("utf-8"))
    assert "эталон снят пульсом" in snap["note"]
    out = drift.check(instance, transport=Site({URL: requests.exceptions.ReadTimeout("нет")}))
    assert out["critical"] == 0 and out["findings"][0]["level"] == "не проверено"


def test_redirect_is_critical(project):
    instance = _baseline(project)
    out = drift.check(instance, transport=Site({URL: Page(301, "", location="https://example.ru/blog/new")}))
    assert out["critical"] == 1 and out["findings"][0]["what"] == "перенаправление"


def test_moved_corpus_address_without_reference_is_not_critical(project):
    instance = Instance(project, today=TODAY)
    out = drift.check(instance, transport=Site({URL: Page(301, "", location="https://example.ru/ecology/new")}),
                      extra_urls=[URL])
    assert out["critical"] == 0
    assert out["findings"][0]["what"] == "адрес в корпусе устарел" and out["findings"][0]["level"] == "важно"
