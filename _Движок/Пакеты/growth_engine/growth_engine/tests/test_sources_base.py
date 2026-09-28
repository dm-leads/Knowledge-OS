from datetime import date

import pytest
import requests

from growth_engine.sources.base import (AccessDenied, EndpointNotAllowed, Query, ReadOnlyClient, SourceError,
                                        load_secrets)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(transport):
    return ReadOnlyClient("https://api.example/v1", ["data"], {"Api-key": "x"}, transport=transport,
                          sleep=lambda seconds: None)


def test_endpoint_outside_allowlist_is_not_sent():
    transport = FakeTransport(FakeResponse(200, {"ok": True}))
    with pytest.raises(EndpointNotAllowed):
        client(transport).call("POST", "orders/delete", json={})
    assert transport.calls == []


@pytest.mark.parametrize("status", [401, 403])
def test_access_refusal_is_not_empty_data(status):
    with pytest.raises(AccessDenied):
        client(FakeTransport(FakeResponse(status))).call("POST", "data", json={})


def test_server_error_is_retried_then_returns():
    transport = FakeTransport(FakeResponse(500), FakeResponse(429), FakeResponse(200, {"data": [1]}))
    assert client(transport).call("POST", "data", json={}) == {"data": [1]}
    assert len(transport.calls) == 3


def test_persistent_network_failure_is_source_error():
    errors = [requests.exceptions.ConnectionError("нет сети") for _ in range(3)]
    with pytest.raises(SourceError):
        client(FakeTransport(*errors)).call("GET", "data")


def test_no_content_is_empty_answer_not_error():
    assert client(FakeTransport(FakeResponse(204))).call("GET", "data") == {}


def test_other_http_error_is_source_error_not_access_denied():
    with pytest.raises(SourceError) as e:
        client(FakeTransport(FakeResponse(404))).call("GET", "data")
    assert not isinstance(e.value, AccessDenied)


def test_query_requires_explicit_breakdown():
    with pytest.raises(TypeError):
        Query(metric="leads", scope="p1", flow="all", period_start=date(2026, 8, 1), period_end=date(2026, 9, 1))


def test_query_requires_fetch_date():
    with pytest.raises(TypeError):
        Query(metric="leads", scope="p1", flow="all", period_start=date(2026, 8, 1), period_end=date(2026, 9, 1),
              breakdown=None)


def test_missing_secret_named_without_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("API_KEY_X=very-secret-value\n", encoding="utf-8")
    assert load_secrets(env, ["API_KEY_X"]) == {"API_KEY_X": "very-secret-value"}
    with pytest.raises(SourceError) as e:
        load_secrets(env, ["API_KEY_X", "TOKEN_Y"])
    assert "TOKEN_Y" in str(e.value) and "very-secret-value" not in str(e.value)


def test_extra_headers_are_merged():
    transport = FakeTransport(FakeResponse(200, {"ok": True}))
    client(transport).call("POST", "data", json={}, headers={"processingMode": "auto"})
    assert transport.calls[0][2]["headers"] == {"Api-key": "x", "processingMode": "auto"}


def test_raw_text_and_queued_report_wait():
    queued = FakeResponse(201)
    queued.headers = {"retryIn": "2"}
    ready = FakeResponse(200)
    ready.text = "CampaignId\tCost\n1\t10\n"
    transport, slept = FakeTransport(queued, ready), []
    reports = ReadOnlyClient("https://api.example/v1", ["reports"], {}, transport=transport, sleep=slept.append)
    assert reports.call("POST", "reports", json={}, raw=True, wait_statuses=(201, 202)) == "CampaignId\tCost\n1\t10\n"
    assert slept == [2.0] and len(transport.calls) == 2
