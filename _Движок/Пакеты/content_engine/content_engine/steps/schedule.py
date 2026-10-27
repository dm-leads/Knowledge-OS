"""Расписание одной командой (Т42.1–Т42.2): `content-engine schedule install | list | remove`.

Задания — из профиля (`schedule.jobs`), каждое запускается с `--unattended`: всё, что требует человека или денег,
останавливается без действия; платных вызовов ноль (Т17.2). Установка — запись за пределы инстанса: право
`schedule.allow: write`, без `--apply` — план, с `--apply` — снимок «до» (что стоит сейчас), установка и чтение обратно
(«записано N — подтверждено N»). `list` — только чтение: свои задания и последний запуск по журналу запусков инстанса
(`data/schedule_runs.jsonl` — его пишет каждая команда с `--unattended`). `remove` снимает только свои задания.
"""
from __future__ import annotations

import json

from ..core import actions as actions_mod
from ..core import rights, store
from ..core.actions import Action
from ..core.errors import EngineError, RightsRefused
from ..schedulers import base

RUNS = "data/schedule_runs.jsonl"


def scheduler(instance, runner=None):
    kind = instance.p["schedule"]["kind"]
    kwargs = {"on_boot": instance.p["schedule"]["on_boot"]}
    if runner is not None:
        kwargs["runner"] = runner
    if kind == "windows":
        from ..schedulers.windows import WindowsScheduler
        return WindowsScheduler(**kwargs)
    if kind == "cron":
        from ..schedulers.cron import CronScheduler
        return CronScheduler(**kwargs)
    from ..core.plugins import load_class
    return load_class(instance, kind, {}, "schedule.kind")(**kwargs)


def _install_action(instance, sch, job: base.Job) -> Action:
    if not base.safe_name(job.name):
        raise EngineError(f"имя задания «{job.name}» — только латиница, цифры и «-»")

    def do():
        code, out = sch.install(job)
        if code != 0:
            raise EngineError(f"{job.name}: планировщик ответил кодом {code}: {out.strip()[:300]}")
        return code

    def verify(_):
        got = sch.read(job.name)
        ok = sch.matches(job, got)
        return ok, f"{job.name}: " + ("стоит, команда и время совпали" if ok else f"прочитано иначе: {got}")

    def snapshot():
        whole = sch.table() if hasattr(sch, "table") else json.dumps(sch.read(job.name), ensure_ascii=False)
        return {"exists": job.name in sch.list(), "read_from": f"планировщик {sch.kind}", "content": whole}

    return Action("schedule_install", job.name, sch.kind, f"{job.every} {job.at} {job.weekday} — {job.command}",
                  "read_back", ("schedule", "write"), do=do, verify=verify, snapshot=snapshot)


def install(instance, *, apply: bool, runner=None) -> dict:
    jobs = base.jobs(instance)
    if not jobs:
        return {"line": "заданий в профиле нет (schedule.jobs пуст)", "outcome": None}
    sch = scheduler(instance, runner)
    planned = [_install_action(instance, sch, j) for j in jobs]
    allowed, refused = rights.split(instance, planned)
    for line in refused:
        print(f"  не в плане — {line}")
    if apply and not allowed:
        raise RightsRefused("расписание не поставлено: нужно разрешение schedule.allow = write")
    out = actions_mod.run(instance, "schedule", allowed if apply else planned, apply=apply)
    return {"line": out.line() if apply else "план напечатан", "outcome": out}


def remove(instance, *, job: str | None, apply: bool, runner=None) -> dict:
    sch = scheduler(instance, runner)
    mine = [n for n in sch.list() if base.own(n, instance)]
    if job:
        mine = [n for n in mine if n == f"{base.PREFIX}-{base.instance_id(instance)}-{job}"]
    planned = []
    for name in mine:
        def do(name=name):
            code, out = sch.remove(name)
            if code != 0:
                raise EngineError(f"{name}: планировщик ответил кодом {code}: {out.strip()[:300]}")
            return code
        planned.append(Action("schedule_remove", name, sch.kind, "снять своё задание", "read_back",
                              ("schedule", "write"), do=do,
                              verify=lambda _, name=name: (name not in sch.list(), f"{name}: снято"),
                              snapshot=lambda name=name: {"exists": True, "read_from": f"планировщик {sch.kind}",
                                                          "content": json.dumps(sch.read(name), ensure_ascii=False)}))
    if not planned:
        return {"line": "своих заданий нет — снимать нечего"}
    allowed, refused = rights.split(instance, planned)
    for line in refused:
        print(f"  не в плане — {line}")
    if apply and not allowed:
        raise RightsRefused("задания не сняты: нужно разрешение schedule.allow = write")
    out = actions_mod.run(instance, "schedule", allowed if apply else planned, apply=apply)
    return {"line": out.line() if apply else "план напечатан"}


def listing(instance, runner=None) -> list[str]:
    sch = scheduler(instance, runner)
    runs = store.read_jsonl(instance.root / RUNS, missing_ok=True)
    lines = []
    for name in sch.list():
        if not base.own(name, instance):
            continue
        job = name[len(f"{base.PREFIX}-{base.instance_id(instance)}-"):]
        last = [r for r in runs if r.get("command") == _command_of(instance, job)]
        tail = f"последний запуск {last[-1]['start']} — код {last[-1]['code']}" if last else "запусков в журнале нет"
        lines.append(f"{name}: {tail}")
    return lines or ["своих заданий в планировщике нет"]


def _command_of(instance, job: str) -> str:
    return next((j["command"] for j in instance.p["schedule"]["jobs"] if j["name"] == job), job)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("schedule", help="расписание: install | list | remove")
    actions = parser.add_subparsers(dest="action", required=True)
    for name, text in (("install", "поставить задания профиля; без --apply — план"),
                       ("list", "свои задания и последний запуск (только чтение)"),
                       ("remove", "снять свои задания; без --apply — план")):
        p = actions.add_parser(name, help=text)
        add_profile(p)
        if name != "list":
            p.add_argument("--apply", action="store_true", help="записать в планировщик (нужно schedule.allow: write)")
        if name == "remove":
            p.add_argument("--job", help="одно задание по имени из профиля")
        p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    if args.action == "list":
        instance = open_instance(args, read_only=True)
        print("\n".join(listing(instance)))
        return 0
    instance = open_instance(args)
    if args.action == "install":
        print(install(instance, apply=args.apply)["line"])
        return 0
    print(remove(instance, job=args.job, apply=args.apply)["line"])
    return 0
