"""Индексация (Т24, П36, П37): одна заявка в сутки, файл ключа IndexNow, предел переобхода до отправки, подтверждение."""
import json

import pytest
import requests

from content_engine.cli import main
from content_engine.cli import open_instance as REAL_OPEN
from content_engine.core import publications
from content_engine.core.instance import Instance
from content_engine.steps import indexing

from .conftest import TODAY, set_profile
from .site_fixture import Page

KEY = "0123456789abcdef0123456789abcdef"
TID = "T0001"


class Net:
    """Подмена сети: сайт (файл ключа), IndexNow, Вебмастер; журнал запросов."""

    def __init__(self, *, key_file=True, indexnow=(202, 200), quota=5, silent=False):
        self.key_file, self.indexnow, self.quota, self.silent = key_file, list(indexnow), quota, silent
        self.log = []

    def __call__(self, method, url, json=None, **kw):
        self.log.append((method, url))
        if url.endswith(f"/{KEY}.txt"):
            return Page(200, KEY) if self.key_file else Page(404, "нет")
        if url.endswith("/indexnow"):
            if self.silent:
                raise requests.exceptions.ConnectionError("нет ответа")
            return Page(self.indexnow.pop(0), "")
        if url.endswith("/recrawl/quota"):
            return Page(200, '{"daily_quota": 100, "quota_remainder": %d}' % self.quota)
        if url.endswith("/recrawl/queue") and method == "POST":
            return Page(202, '{"task_id": "z-1"}')
        if "/recrawl/queue/" in url:
            return Page(200, '{"state": "IN_PROGRESS"}')
        return Page(404, "нет")


def setup(project, net, *, allow="write", indexnow=True, webmaster=False, monkeypatch=None):
    monkeypatch.setenv("INDEXNOW_KEY", KEY)
    monkeypatch.setenv("YANDEX_WEBMASTER_TOKEN", "токен")
    set_profile(project, lambda d: (d["indexing"].update(allow=allow),
                                    d["indexing"]["indexnow"].update(enabled=indexnow),
                                    d["indexing"]["webmaster"].update(enabled=webmaster),
                                    d["sources"]["webmaster"].update(enabled=webmaster, user_id="1", host_id="h")))
    inst = Instance(project, today=TODAY, transport=net)
    publications.log(inst, "published", id=TID, url="example.ru/blog/posadka-chesnoka", day=TODAY.isoformat(),
                     built=TODAY.isoformat())
    import content_engine.cli as cli
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: REAL_OPEN(args, today=TODAY, transport=net))
    return inst


def sent(net):
    return [u for m, u in net.log if m == "POST"]


def test_indexnow_202_confirmed_and_second_same_day_not_sent(project, monkeypatch):
    net = Net()
    inst = setup(project, net, monkeypatch=monkeypatch)
    out = indexing.submit(inst, TID, apply=True)
    assert out["confirmed"] == 2 and len(sent(net)) == 2
    rows = [r for r in publications.read(inst) if r["event"] == "indexing"]
    assert {r["system"] for r in rows} == {"indexnow_yandex", "indexnow_bing"} and all(r["confirmed"] for r in rows)
    out = indexing.submit(Instance(project, today=TODAY, transport=net), TID, apply=True)
    assert len(sent(net)) == 2 and any("сегодня уже отправлено" in line for line in out["lines"])


def test_files_level_plans_only(project, monkeypatch):
    net = Net()
    setup(project, net, allow="files", monkeypatch=monkeypatch)
    assert main(["index", "submit", TID, "--profile", str(project), "--apply"]) == 1
    assert sent(net) == []


def test_no_key_file_not_connected(project, monkeypatch, capsys):
    net = Net(key_file=False)
    setup(project, net, monkeypatch=monkeypatch)
    assert main(["index", "submit", TID, "--profile", str(project), "--apply"]) == 0
    assert "IndexNow: не подключено" in capsys.readouterr().out and sent(net) == []


def test_indexnow_without_answer_or_error_is_code_3(project, monkeypatch):
    net = Net(silent=True)
    setup(project, net, monkeypatch=monkeypatch)
    assert main(["index", "submit", TID, "--profile", str(project), "--apply"]) == 3
    rows = [r for r in publications.read(Instance(project, today=TODAY)) if r["event"] == "indexing"]
    assert len(rows) == 2 and not any(r["confirmed"] for r in rows)
    # заявка могла уйти — в те же сутки повтора нет
    assert main(["index", "submit", TID, "--profile", str(project), "--apply"]) == 0


def test_indexnow_error_status_is_code_3(project, monkeypatch):
    setup(project, Net(indexnow=(500, 500)), monkeypatch=monkeypatch)
    assert main(["index", "submit", TID, "--profile", str(project), "--apply"]) == 3


def test_webmaster_quota_counted_before_send(project, monkeypatch):
    net = Net(quota=0)
    inst = setup(project, net, indexnow=False, webmaster=True, monkeypatch=monkeypatch)
    out = indexing.submit(inst, TID, apply=True)
    assert sent(net) == [] and any("предел переобхода выбран" in line for line in out["lines"])
    net = Net(quota=3)
    inst = setup(project, net, indexnow=False, webmaster=True, monkeypatch=monkeypatch)
    out = indexing.submit(inst, TID, apply=True)
    assert out["confirmed"] == 1 and any("z-1" in line and "IN_PROGRESS" in line for line in out["lines"])


def test_submit_before_arrival_refused(project, monkeypatch):
    from content_engine.core.errors import GateRefused
    monkeypatch.setenv("INDEXNOW_KEY", KEY)
    with pytest.raises(GateRefused, match="не подтверждено"):
        indexing.submit(Instance(project, today=TODAY, transport=Net()), "T0404", apply=True)


def test_status_without_sources_not_connected(project, monkeypatch):
    net = Net()
    setup(project, net, indexnow=False, monkeypatch=monkeypatch)
    out = indexing.status(Instance(project, today=TODAY), all_days=True)
    assert "Google: не подключено" in out["lines"][0] and "Яндекс: не подключено" in out["lines"][0]
    assert json.dumps(out, ensure_ascii=False)
