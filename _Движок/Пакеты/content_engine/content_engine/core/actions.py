"""Действия записи вовне (Т16.3): план без `--apply`, запись и подтверждение каждого действия с `--apply`.

Действие несёт ступень прав (`right`), способ подтверждения (`confirm`: `read_back` — чтение обратно, `status_code` —
код ответа сервиса 200/202 в журнале, `ticket` — id заявки и её статус чтением) и, если пишет в живую страницу, CMS или
базу проекта, — функцию снимка «до». Порядок с `--apply`: снимки «до» всех действий → запись → подтверждение. Нет снимка
у действия, которому он нужен, — ни одной записи (`SnapshotMissing`). Итог печатается «записано N — подтверждено N»;
неподтверждённая запись — `WriteUnconfirmed` (код 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import snapshots
from .errors import EngineError, SnapshotMissing, WriteUnconfirmed

CONFIRMS = ("read_back", "status_code", "ticket")


@dataclass
class Action:
    kind: str                                  # что делаем: publish_file, commit, cms_draft, indexnow, db_row …
    target: str                                # куда: адрес, путь, система
    method: str                                # как: PUT, POST, git commit …
    summary: str                               # что именно, словами
    confirm: str                               # read_back | status_code | ticket
    right: tuple[str, str]                     # (область прав, нужная ступень)
    do: Callable[[], object] | None = None     # выполнить запись; вернуть ответ
    verify: Callable[[object], tuple[bool, str]] | None = None   # подтвердить по ответу или чтением обратно
    snapshot: Callable[[], dict] | None = None # текущее состояние цели: {exists, content, read_from, meta}
    needs_snapshot: bool = True                # живая страница, CMS, база — да; заявка в IndexNow — нет
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.confirm not in CONFIRMS:
            raise EngineError(f"действие {self.kind}: подтверждение «{self.confirm}» не из {CONFIRMS}")

    def line(self) -> str:
        return f"{self.kind} → {self.target} ({self.method}): {self.summary}; подтверждение — {self.confirm}"


@dataclass
class Outcome:
    planned: int
    written: int = 0
    confirmed: int = 0
    run_id: str = ""
    details: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"записано {self.written} — подтверждено {self.confirmed}"


def run(instance, tid: str, actions: list[Action], *, apply: bool, run_id: str | None = None,
        echo: Callable[[str], None] = print, must_exist: bool = False) -> Outcome:
    """`must_exist` — правка страницы (Т36.3): цель каждого действия со снимком должна существовать до записи;
    исчезла — отказ без единой записи, а не новая страница."""
    out = Outcome(planned=len(actions))
    if not apply:
        for action in actions:
            echo(f"  план: {action.line()}")
        echo(f"план: действий {len(actions)}, запросов записи 0 — выполнить с --apply")
        return out
    if not actions:
        return out
    from . import rights
    for action in actions:                     # ступень проверяется и здесь, а не только вызывающим кодом
        rights.require(instance, *action.right)
    missing = [a.kind for a in actions if a.needs_snapshot and a.snapshot is None]
    if missing:
        raise SnapshotMissing(f"у действий {missing} нет снимка «до» — запись не выполняется")
    states = [(action, action.snapshot()) for action in actions if action.snapshot is not None]
    if must_exist:
        gone = [a.target for a, state in states if not state.get("exists")]
        if gone:
            from .errors import GateRefused
            raise GateRefused(f"правка страницы: цели нет ({', '.join(gone)}) — новая страница не создаётся, "
                              "запись не выполнена")
    out.run_id = run_id or snapshots.new_run_id(instance)
    for action, state in states:
        snapshots.save(instance, tid, out.run_id, target=action.target, kind=action.kind,
                       read_from=state.get("read_from", action.target), exists=bool(state.get("exists")),
                       content=state.get("content"), meta=state.get("meta"))
    stopped = False
    for action in actions:
        if action.do is None or action.verify is None:
            raise EngineError(f"действие {action.kind} без исполнителя или подтверждения")
        if stopped:
            out.details.append(f"✗ {action.kind} → {action.target}: не выполнено — предыдущая запись не подтверждена")
            continue
        out.written += 1                       # запрос мог уйти и при обрыве ответа — считается отправленным
        try:
            ok, detail = action.verify(action.do())
        except Exception as exc:              # запрос мог уйти: любая ошибка после отправки — неподтверждённая запись
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        out.confirmed += bool(ok)
        out.details.append(f"{'✓' if ok else '✗'} {action.kind} → {action.target}: {detail}")
        # следующие записи зависят от этой (коммит → push), кроме независимых заявок (IndexNow, переобход)
        stopped = not ok and not action.meta.get("independent")
    for line in out.details:
        echo(f"  {line}")
    echo(out.line())
    if out.confirmed < out.written:
        error = WriteUnconfirmed(f"{out.line()}: " + "; ".join(d for d in out.details if d.startswith("✗")))
        error.outcome = out                    # что подтверждено — шаг всё равно пишет в журнал
        raise error
    return out
