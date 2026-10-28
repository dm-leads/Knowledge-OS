"""О1 волны 3: тайм-аут сети в «доехало» — не «не доехало», а «не проверено» с повтором; день выкладки не пишется."""
import pytest
import requests

from content_engine.core import publications
from content_engine.core.urls import norm_url
from content_engine.steps import known_answers as ka
from content_engine.steps import verify as verify_mod

from .site_fixture import Page
from .test_brief import TID, inst  # noqa: F401
from .test_verify import URL, at, built


class Flaky:
    """Сайт, который первые `fail` запросов не отвечает (тайм-аут), потом отдаёт страницы."""

    def __init__(self, fail: int, pages: dict, exc=requests.exceptions.ReadTimeout):
        self.fail, self.pages, self.exc, self.calls = fail, pages, exc, 0

    def __call__(self, method, url, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise self.exc("нет ответа")
        return self.pages.get(norm_url(url), Page(404, "нет"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(verify_mod, "SLEEP", slept.append)
    return slept


def test_timeout_then_answer_arrives(inst, project, no_sleep):  # noqa: F811
    site = Flaky(2, {norm_url(URL): Page(200, built(inst))})
    report = verify_mod.verify(at(project, site), TID, URL, browser=False)
    assert report["verdict"] == "доехало", report["items"]
    assert site.calls == 3 and no_sleep == [20, 20]                     # два повтора с паузой профиля


@pytest.mark.parametrize("exc", [requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError])
def test_never_answers_is_unverified_not_failed(inst, project, exc, capsys):  # noqa: F811
    built(inst)
    site = Flaky(99, {}, exc)
    instance = at(project, site)
    code = verify_mod.run_verify(instance, TID, URL, browser=False)
    out = capsys.readouterr().out
    assert code == 3
    assert "НЕ ПРОВЕРЕНО" in out and "не доехало" not in out.lower().replace("не проверено", "")
    events = [r["event"] for r in publications.read(instance)]
    assert "unverified" in events and "published" not in events     # день выкладки не записан
    assert not (project.parent / "data" / "snapshots" / "live").exists()


def test_http_error_is_still_not_arrived(inst, project):  # noqa: F811
    built(inst)
    site = Flaky(0, {})                                                   # сайт ответил 404 — это ответ
    report = verify_mod.verify(at(project, site), TID, URL, browser=False)
    assert report["verdict"] == "не доехало"


def test_known_answer_publish_case_is_pending_on_network(inst, project, tmp_path, capsys):  # noqa: F811
    built(inst)
    cases = tmp_path / "ka.yaml"
    cases.write_text(f"cases:\n  - name: доехала\n    kind: publish\n    id: {TID}\n    url: {URL}\n"
                     f"    expect: доехало\n", encoding="utf-8")
    instance = at(project, Flaky(99, {}))
    good, detail = ka.run_case(instance, ka.load_cases(instance, str(cases))[0], True)
    assert good is None and "не проверено" in detail
