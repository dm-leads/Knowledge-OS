"""Находки проверки кода волны 3 (З66): секрет вебхука, proxy в итоге, cron, «не проверено» в вердикте и сводке,
расписание без броней, перенос непроверенных находок аудита, имена заданий, перемер, мелкие."""
import json

import pytest
import requests
import yaml

from content_engine.cli import main
from content_engine.core import audit_store, bets, profile as profile_mod, store
from content_engine.core.errors import EngineError, ProfileError
from content_engine.core.instance import Instance
from content_engine.core.verdict import decide
from content_engine.schedulers.cron import CronScheduler
from content_engine.steps import audit, bets_cmd, pulse, schedule

from .conftest import TODAY, set_profile
from .site_fixture import Page

HOOK = "https://open.larksuite.example/open-apis/bot/v2/hook/SECRET123"


def test_lark_webhook_path_not_in_output(project, monkeypatch, capsys):
    monkeypatch.setenv("NOTIFY_LARK_WEBHOOK", HOOK)
    set_profile(project, lambda d: d["notify"].update(kind="lark_webhook", url_env="NOTIFY_LARK_WEBHOOK", allow="write"))

    def down(*a, **k):
        raise requests.exceptions.ConnectionError("нет сети")
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, today=TODAY, transport=down, **kw))
    assert main(["pulse", "run", "--no-network", "--profile", str(project)]) == 0
    capsys.readouterr()
    assert main(["pulse", "notify", "--apply", "--profile", str(project)]) == 3
    out = capsys.readouterr()
    assert "SECRET123" not in out.out + out.err and "<вебхук>" in out.out + out.err


def test_proxy_never_decides_even_with_step_three():
    rec = {"bet_missing": False, "expectation_numbers": [{"value": 1, "unit": "₽"}], "fail_threshold": "0"}
    got, _ = bets.decide(rec, {"step": 3, "money": {"value": 999, "status": "proxy"}, "leads": None}, 3)
    assert got == bets.LOW_DATA
    data = yaml.safe_load(profile_mod.template_text())
    data["bets"]["min_step"] = 3
    with pytest.raises(ProfileError, match="bets.min_step"):
        profile_mod.validate(data)


def test_cron_read_error_writes_nothing_and_snapshot_is_whole_table():
    writes = []

    def broken(args, input_text=None):
        if args == ["crontab", "-l"]:
            return 1, "crontab: permission denied"
        writes.append(args)
        return 0, ""
    with pytest.raises(EngineError, match="не прочитана"):
        CronScheduler(runner=broken).install(type("J", (), {"name": "KOS-content-engine-x-pulse", "at": "09:00",
                                                             "every": "day", "weekday": "", "workdir": "/w",
                                                             "command": "c"})())
    assert writes == []
    table = "0 1 * * * чужое\n5 9 * * * своё  # KOS-content-engine-x-pulse"
    ok = CronScheduler(runner=lambda a, input_text=None: (0, table))
    assert "чужое" in ok.table()
    empty = CronScheduler(runner=lambda a, input_text=None: (1, "no crontab for user"))
    assert empty.list() == []


def test_unchecked_in_verdict_and_summary(project):
    instance = Instance(project, today=TODAY)
    got = decide(instance, {"site_ok": True, "last_publication": TODAY.isoformat(), "drift_critical": 0,
                            "audit_critical": 0, "audit_age": 1, "stock_days": 10,
                            "unchecked": ["дрифт: сеть не ответила у 3 стр."]})
    assert got["color"] == "зелёный" and "дрифт: сеть не ответила у 3 стр." in got["unchecked"]
    text = pulse.summary({"date": TODAY.isoformat(), "verdict": got, "diff": []})
    assert "Не проверено: дрифт" in text


def test_unattended_does_not_release_reservations(project, monkeypatch):
    from content_engine.steps import reservations
    instance = Instance(project, today=TODAY)
    reservations.create(instance, topic="тема", key=[], cluster_id="C1", who="проверка")
    records = reservations.load(instance)
    records[0]["expires"] = "2026-01-01"                                   # истекла — освобождать можно, но не здесь
    reservations.save(instance, records)
    before = (project.parent / "data" / "reservations.json").read_bytes()
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, **{"today": TODAY, **kw}))
    assert main(["pulse", "run", "--no-network", "--unattended", "--profile", str(project)]) == 0
    assert (project.parent / "data" / "reservations.json").read_bytes() == before


def test_job_name_with_dash_listed_and_removed_exactly(project):
    from .test_schedule_week import FakeSchtasks
    set_profile(project, lambda d: d["schedule"].update(allow="write", jobs=[
        {"name": "week-plan", "command": "week plan", "every": "week", "at": "10:00", "weekday": "mon"},
        {"name": "plan", "command": "pulse run", "every": "day", "at": "09:00", "weekday": ""}]))
    fake = FakeSchtasks()
    instance = Instance(project, today=TODAY)
    schedule.install(instance, apply=True, runner=fake)
    store.append_jsonl(project.parent / "data" / "schedule_runs.jsonl",
                       [{"command": "week plan", "start": "2026-03-02T10:00:01", "code": 0}])
    lines = schedule.listing(Instance(project, today=TODAY), runner=fake)
    assert any(l.endswith("-week-plan: последний запуск 2026-03-02T10:00:01 — код 0") for l in lines), lines
    schedule.remove(instance, job="plan", apply=True, runner=fake)
    assert any(n.endswith("-week-plan") for n in fake.tasks) and not any(n.endswith("-plan") and
                                                                         not n.endswith("week-plan") for n in fake.tasks)


def test_low_data_window_is_remeasured(project):
    from .test_bets_wave3 import money_file, record, window
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record()])
    money_file(project, [])
    bets_cmd.measure(Instance(project, today=TODAY))
    assert bets.load(instance)[0]["measures"]["90"]["proposed"] == bets.LOW_DATA
    money_file(project, [("otoplenie-teplicy", 30000, 5, *window("2025-10-01", 90))])     # выгрузка пришла позже
    bets_cmd.measure(Instance(project, today=TODAY))
    assert bets.load(instance)[0]["measures"]["90"]["proposed"] == "сыграла"


def test_unreachable_page_findings_carried_not_gone(project):
    from .test_audit import D, Site, html
    (project.parent / "sitemap.txt").write_text(f"{D}/blog/a", encoding="utf-8")
    set_profile(project, lambda d: d["site"].update(sitemap_file="sitemap.txt"))
    instance = Instance(project, today=TODAY)
    audit.write_checklist(instance, apply=True)
    first = audit.run_audit(Instance(project, today=TODAY, transport=Site({f"{D}/blog/a": Page(200, html(
        title="", url=f"{D}/blog/a")), f"{D}/robots.txt": Page(200, "")})))
    audit_store.write(instance, "full", 1, first["findings"])
    from datetime import timedelta

    class Down(Site):
        def __call__(self, method, url, **kw):
            if url.endswith("/blog/a"):
                raise requests.exceptions.ReadTimeout("нет")
            return super().__call__(method, url, **kw)
    later = Instance(project, today=TODAY + timedelta(days=7), transport=Down({f"{D}/robots.txt": Page(200, "")}))
    second = audit.run_audit(later)
    d = audit.delta(second["previous"], second["findings"])
    assert d["gone"] == [] and any(f["detail"].startswith("не проверено") for f in second["findings"])


def test_project_notify_adapter_cannot_bypass_right(project, tmp_path):
    folder = project.parent / "adapters"
    folder.mkdir()
    (folder / "notify_x.py").write_text(
        "from content_engine.core.actions import Action\n"
        "class X:\n"
        "    def __init__(self, instance): pass\n"
        "    def action(self, text):\n"
        "        return Action('x', 't', 'POST', 's', 'status_code', ('indexing', 'write'), needs_snapshot=False)\n",
        encoding="utf-8")
    set_profile(project, lambda d: d["notify"].update(kind="module:adapters/notify_x.py:X", url_env="X_URL"))
    from content_engine.adapters import notify
    with pytest.raises(EngineError, match="notify, write"):
        notify.action(Instance(project, today=TODAY), "текст")


def test_broken_card_yaml_is_engine_error(project):
    from content_engine.adapters.growth import FileGrowth
    folder = project.parent / "growth"
    folder.mkdir()
    (folder / "H-1.yaml").write_text("id: [незакрыто\n", encoding="utf-8")
    with pytest.raises(EngineError, match="YAML"):
        FileGrowth(folder, TODAY).decision("H-1")


def test_contours_check_is_read_only(project):
    import hashlib
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in project.parent.rglob("*") if p.is_file()}
    assert main(["check", "--contours", "--profile", str(project)]) == 0
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in project.parent.rglob("*") if p.is_file()}
    assert before == after


def test_bet_for_corpus_article_keeps_measures(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [{"id": "C:otoplenie-teplicy", "origin": "corpus",
        "url": "example.ru/blog/otoplenie-teplicy", "published": "2025-10-01",
        "measures": {"90": {"proposed": "ставки нет", "result": "ставки нет"}}}])
    bets.add(instance, {"id": "T0009", "topic": "т", "bet": {}}, "example.ru/blog/otoplenie-teplicy", "2025-10-01")
    recs = bets.load(instance)
    assert [r["id"] for r in recs] == ["T0009"] and recs[0]["measures"]["90"]["result"] == "ставки нет"


def test_real_bet_remeasured_after_corpus_record(project):
    from .test_bets_wave3 import money_file, window
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [{"id": "C:otoplenie-teplicy", "origin": "corpus",
        "url": "example.ru/blog/otoplenie-teplicy", "published": "2025-10-01", "bet_missing": True,
        "measures": {"90": {"proposed": "ставки нет", "result": "ставки нет"}}}])
    topic = {"id": "T0009", "topic": "т", "direction": "теплицы", "bet": {
        "hypothesis": "h", "hypothesis_id": "H-1", "expectation": "4 заявки", "fail_threshold": "0",
        "expectation_numbers": [{"value": 4, "unit": "заявки"}], "unit": "article", "planned_count": 1}}
    bets.add(instance, topic, "example.ru/blog/otoplenie-teplicy", "2025-10-01")
    rec = bets.load(instance)[0]
    assert "measures" not in rec and rec["measures_corpus"]["90"]["result"] == "ставки нет"
    money_file(project, [("otoplenie-teplicy", 30000, 5, *window("2025-10-01", 90))])
    bets_cmd.measure(Instance(project, today=TODAY))
    assert bets.load(instance)[0]["measures"]["90"]["proposed"] == "сыграла"


def test_pulse_survives_google_auth_failure(project, monkeypatch):
    """Упавший источник не мешает остальным блокам: ошибка авторизации Google — «источник не ответил»."""
    import content_engine.adapters.gsc as gsc
    from google.auth.exceptions import TransportError

    def no_token(key_file):
        raise TransportError("нет сети")
    monkeypatch.setattr(gsc, "service_account_token", no_token)
    set_profile(project, lambda d: d["sources"]["gsc"].update(enabled=True))
    from content_engine.core import positions
    snap = positions.snapshot(Instance(project, today=TODAY), transport=lambda *a, **k: Page(404, ""))
    assert snap["engines"]["google"]["status"] == "источник не ответил"


def test_audit_without_network_writes_nothing(project):
    from content_engine.core.errors import NetworkUnreachable
    set_profile(project, lambda d: d["site"].update(sitemap_url="https://example.ru/sitemap.xml"))
    instance = Instance(project, today=TODAY)
    audit.write_checklist(instance, apply=True)

    def down(*a, **k):
        raise requests.exceptions.ConnectionError("нет сети")
    with pytest.raises(NetworkUnreachable, match="аудит не проведён"):
        audit.run_audit(Instance(project, today=TODAY, transport=down))
    assert audit_store.latest(instance) is None


def test_any_transport_error_hides_secret(project, monkeypatch):
    """Codex №1–2: секрет очищается из текста любого исключения транспорта, не только ошибки машины."""
    from content_engine.adapters import notify
    from content_engine.core.errors import EngineError as EE
    monkeypatch.setenv("NOTIFY_LARK_WEBHOOK", HOOK)
    set_profile(project, lambda d: d["notify"].update(kind="lark_webhook", url_env="NOTIFY_LARK_WEBHOOK"))

    def leaky(method, url, **kw):
        raise RuntimeError(url)
    act = notify.action(Instance(project, today=TODAY), "текст", transport=leaky)
    with pytest.raises(EE) as caught:
        act.do()
    assert "SECRET123" not in str(caught.value) and "<вебхук>" in str(caught.value)


def test_notify_second_process_waits_for_lock(project, monkeypatch):
    """Codex №8: вторая отправка в тот же момент получает отказ блокировки, а не второе сообщение."""
    from content_engine.core import locks
    from content_engine.core.errors import LockBusy
    instance = Instance(project, today=TODAY)
    pulse.save(instance, {"date": TODAY.isoformat(), "verdict": {"color": "зелёный", "why": "", "todo": "",
                                                                    "unchecked": []}, "diff": [], "blocks": {}})
    with locks.held(instance, "pulse", "notify"):
        with pytest.raises(LockBusy):
            pulse.run_notify(Instance(project, today=TODAY), apply=True)
