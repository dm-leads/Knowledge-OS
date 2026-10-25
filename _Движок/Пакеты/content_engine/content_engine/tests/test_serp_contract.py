"""Контракт источника выдачи (Т4.1, П3) и правила клиента «только чтение» (Т4)."""
import pytest
import requests

from content_engine.adapters.base import ReadOnlyClient
from content_engine.adapters.fake import FakeSerp
from content_engine.adapters.keysso import KeyssoSerp
from content_engine.adapters.serp import NOT_IN_BASE, OK
from content_engine.core.errors import AccessDenied, EndpointNotAllowed, QuotaExhausted, SourceError

from .keysso_transport import FIXTURE, KeyssoTransport, Response


def no_sleep(_):
    pass


def make_keysso(**kwargs):
    return KeyssoSerp("тестовый-ключ", transport=KeyssoTransport(**kwargs), sleep=no_sleep)


SOURCES = {"keysso": make_keysso, "fake": lambda: FakeSerp(FIXTURE)}


@pytest.fixture(params=sorted(SOURCES))
def source(request):
    return SOURCES[request.param]()


# --- контракт: один набор на обе реализации ---

def test_top_first_ten_sorted(source):
    result = source.top(["отопление теплицы"], "msk")["отопление теплицы"]
    assert result.status == OK
    assert len(result.top) == 10 and [p for _, p in result.top] == list(range(1, 11))
    assert result.top[1] == ("example.ru/blog/otoplenie-teplicy", 2)


def test_phrase_not_in_base_is_status_not_empty(source):
    result = source.top(["несуществующая фраза"], "msk")["несуществующая фраза"]
    assert result.status == NOT_IN_BASE and result.top == []


def test_site_keywords_full_urls(source):
    rows = source.site_keywords("example.ru", "msk", 100)
    assert len(rows) == 5
    assert {r.url for r in rows} == {"example.ru/blog/otoplenie-teplicy", "example.ru/blog/ukrytie-roz",
                                     "example.ru/blog/gazon-vesnoj"}
    first = next(r for r in rows if r.query == "отопление теплицы")
    assert (first.pos, first.frequency, first.bid) == (2, 900, 12.5)


def test_site_keywords_limit(source):
    assert len(source.site_keywords("example.ru", "msk", 3)) == 3


def test_remaining_is_number(source):
    assert source.remaining() == 500


def test_competitor_keywords(source):
    rows = source.competitor_keywords("garden.example", "example.ru", "msk", 10)
    assert [r["query"] for r in rows] == ["полив теплицы", "обрезка малины осенью"]


# --- клиент ---

def test_endpoint_outside_whitelist_not_sent():
    sent = []
    client = ReadOnlyClient("тест", "https://api.example", {"read"}, {}, transport=lambda *a, **k: sent.append(1))
    with pytest.raises(EndpointNotAllowed):
        client.call("GET", "write")
    with pytest.raises(EndpointNotAllowed):
        client.call("DELETE", "read")
    assert sent == []


def test_access_denied_is_its_own_error():
    serp = make_keysso(script={"limits/all": [Response(401, {"message": "нет"})]})
    with pytest.raises(AccessDenied):
        serp.remaining()


def test_retry_only_on_temporary_errors():
    serp = make_keysso(script={"limits/all": [Response(502), Response(200, {"analysis": {"limit": 10,
                                                                                         "usedLimit": 4}})]})
    assert serp.remaining() == 6
    serp = make_keysso(script={"limits/all": [Response(400, {"message": "плохой запрос"})]})
    with pytest.raises(SourceError, match="HTTP 400"):
        serp.remaining()
    assert len(serp.client.transport.log) == 1


def test_timeout_is_retried():
    calls = []

    def flaky(method, url, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.Timeout()
        return Response(200, {"analysis": {"limit": 5, "usedLimit": 0}})

    serp = KeyssoSerp("ключ", transport=flaky, sleep=no_sleep)
    assert serp.remaining() == 5 and len(calls) == 2


def test_report_in_progress_waits():
    waits = []
    serp = KeyssoSerp("ключ", transport=KeyssoTransport(script={"limits/all": [Response(200, {"code": 202})]}),
                      sleep=waits.append)
    assert serp.remaining() == 500
    assert 15 in waits


def test_429_with_exhausted_quota_no_retries():
    transport = KeyssoTransport(script={
        "tools/check-top": [Response(429, {"message": "лимит"})],
        "limits/all": [Response(200, {"analysis": {"limit": 30000, "usedLimit": 30000}})]})
    serp = KeyssoSerp("ключ", transport=transport, sleep=no_sleep)
    with pytest.raises(QuotaExhausted):
        serp.top(["отопление теплицы"], "msk")
    assert [e for _, e in transport.log].count("tools/check-top") == 1


def test_429_rate_limit_one_retry_after_pause():
    waits = []
    transport = KeyssoTransport(script={
        "tools/check-top": [Response(429, {"message": "частота"}), Response(429, {"message": "частота"})]})
    serp = KeyssoSerp("ключ", transport=transport, sleep=waits.append)
    with pytest.raises(QuotaExhausted):
        serp.top(["отопление теплицы"], "msk")
    assert [e for _, e in transport.log].count("tools/check-top") == 2
    assert waits.count(60) == 1


def test_429_rate_limit_then_success():
    transport = KeyssoTransport(script={"tools/check-top": [Response(429, {"message": "частота"})]})
    serp = KeyssoSerp("ключ", transport=transport, sleep=no_sleep)
    assert serp.top(["укрытие роз"], "msk")["укрытие роз"].status == OK


def test_http_202_waits_too():
    waits = []
    serp = KeyssoSerp("ключ", transport=KeyssoTransport(script={"limits/all": [Response(202)]}), sleep=waits.append)
    assert serp.remaining() == 500 and 15 in waits


def test_check_top_404_means_not_in_base():
    serp = make_keysso(script={"tools/check-top": [Response(404, {"message": "не найдено"})]})
    result = serp.top(["редкая фраза", "ещё одна"], "msk")
    assert {r.status for r in result.values()} == {NOT_IN_BASE}
