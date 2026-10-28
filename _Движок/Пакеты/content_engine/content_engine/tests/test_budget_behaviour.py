"""О3 волны 3: бюджет-гейт команд выдачи доказан поведением, а не наличием флагов. Keys.so отказывает, выдачу даёт
платный запасной источник: без «да» — код 2 и ноль платных вызовов; `--unattended` — ноль при любой цене; с
подтверждением — вызов и строка в учёте расхода. Путь — настоящий: `main()` → `open_instance` → команда."""
import base64
import json

import pytest

from content_engine.cli import main
from content_engine.core import store
from content_engine.core.instance import Instance
from content_engine.steps import queue as queue_mod
from content_engine.steps.demand_map import build_map

from .conftest import TODAY, set_profile
from .keysso_transport import Response
from .test_demand_map import setup as map_setup
from .test_queue import SERP, FakeSerp, topic


def _xml(urls):
    body = "<yandexsearch>" + "".join(f"<doc><url>{u}</url></doc>" for u in urls) + "</yandexsearch>"
    return base64.b64encode(body.encode("utf-8")).decode()


class PaidSerpSite:
    """Keys.so отвечает 401, Yandex Search — выдачей; считает платные вызовы."""

    def __init__(self):
        self.paid = 0

    def __call__(self, method, url, params=None, json=None, headers=None, timeout=None, **kw):
        if "api.keys.so" in url:
            return Response(401, {})
        if "searchapi" in url or "yandex" in url:
            self.paid += 1
            return Response(200, {"rawData": _xml([f"https://site{i}.example/a" for i in range(12)])})
        return Response(404, {})


@pytest.fixture
def paid(project, monkeypatch):
    for name in ("KEYSO_API_KEY", "YANDEX_SEARCH_API_KEY", "YANDEX_FOLDER_ID"):
        monkeypatch.setenv(name, "x")
    set_profile(project, lambda d: (d["sources"].update(serp_order=["keysso", "yandex_search"]),
                                    d["sources"]["keysso"].update(enabled=True),
                                    d["sources"]["yandex_search"].update(price_rub=0.4, key_env="YANDEX_SEARCH_API_KEY",
                                                                         folder_env="YANDEX_FOLDER_ID"),
                                    d["budget"].update(run_limit_rub=50, month_limit_rub=500)))
    site = PaidSerpSite()
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, today=TODAY, transport=site, **kw))
    return site


def _prepare_cluster(project):
    instance = map_setup(project)
    store.write_json(instance.file("clusters"), "clusters", [])
    return ["cluster"]


def _prepare_map(project):
    map_setup(project)
    return ["map", "build"]


def _prepare_gate(project):
    return ["gate", "topic", "обрезка малины осенью", "-f", "обрезка малины осенью", "-f", "как обрезать малину", "-f", "когда обрезать малину"]


def _prepare_refill(project):
    map_setup(project)
    build_map(Instance(project, today=TODAY, serp=FakeSerp(SERP)))
    instance = Instance(project, today=TODAY)
    queue_mod.save(instance, [topic("T0001", topic="обрезка малины осенью",
                                    formulations=["обрезка малины осенью", "как обрезать малину", "когда обрезать малину"])])
    return ["queue", "refill"]


def _prepare_known(project):
    (project.parent / "known_answers.yaml").write_text(json.dumps({"cases": [
        {"name": "малина", "kind": "gate", "topic": "обрезка малины осенью",
         "formulations": ["обрезка малины осенью", "как обрезать малину", "когда обрезать малину"], "expect": {"verdict": "свободно"}}]},
        ensure_ascii=False), encoding="utf-8")
    return ["known-answers"]


def _prepare_take(project):
    from .test_prepare_top import TID, mapped_topic
    mapped_topic(project, queue_mod.GATED)
    return ["queue", "take", TID]


def _prepare_retake(project):
    from .test_prepare_top import TID, mapped_topic
    mapped_topic(project, queue_mod.TAKEN)
    return ["queue", "retake", TID]


def _prepare_refresh(project):
    from content_engine.core import store
    from content_engine.core.instance import Instance
    url = "https://example.ru/blog/otoplenie-teplicy"
    store.append_jsonl(Instance(project, today=TODAY).file("positions"), [
        {"date": TODAY.isoformat(), "engine": "google", "query": "отопление теплицы", "url": url, "position": 6.0,
         "impressions": 80}])
    return ["refresh", "take", url]


COMMANDS = {"refresh take": _prepare_refresh, "cluster": _prepare_cluster, "queue take": _prepare_take, "queue retake": _prepare_retake, "map build": _prepare_map, "gate topic": _prepare_gate,
            "queue refill": _prepare_refill, "known-answers": _prepare_known}


def _spend_rows(project):
    path = project.parent / "data" / "spend.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.is_file() else []


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_paid_serp_needs_yes(name, project, paid, capsys):
    cmd = COMMANDS[name](project) + ["--profile", str(project)]
    assert main(cmd) == 2, capsys.readouterr()
    assert paid.paid == 0                                                   # без «да» — ноль платных вызовов
    assert "бюджет-гейт" in capsys.readouterr().err


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_paid_serp_unattended_spends_nothing(name, project, paid):
    cmd = COMMANDS[name](project) + ["--profile", str(project), "--unattended", "--confirm-spend", "50"]
    assert main(cmd) == 2
    assert paid.paid == 0


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_paid_serp_with_yes_calls_and_writes_spend(name, project, paid):
    cmd = COMMANDS[name](project) + ["--profile", str(project), "--confirm-spend", "50"]
    assert main(cmd) != 2
    assert paid.paid > 0
    assert any(r.get("source") == "yandex_search" or "yandex" in json.dumps(r, ensure_ascii=False)
               for r in _spend_rows(project))
