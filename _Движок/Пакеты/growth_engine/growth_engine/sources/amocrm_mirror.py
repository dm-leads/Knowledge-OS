"""Адаптер зеркала amoCRM (класс D): покупатели, оплаты, New First SQL и когорта людей по бренду.

Зеркало — локальная копия сделок amoCRM, открывается только на чтение (страж 11). Деньги отсюда не берутся:
amoCRM не источник истины по деньгам. Бренд — по полю «Компания», при пустом поле — по воронке из конфигурации;
сделки с брендом по воронке считаются в оговорке. Наружу отдаются только агрегаты: в зеркале ПДн клиентов (страж 12).
Дата съёма числа — день синхронизации зеркала: прогон другого дня останавливается (П3).

Когорта людей (П2): контакты, чей первый флаг New First создан в окне; бренд когорты — по сделке с первым флагом.
Покупатель когорты — оплата в воронке продаж не раньше New First и не позже горизонта из конфигурации ядра.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..core.config import InstanceConfig
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, SourceError

SYSTEM = "amocrm_mirror"
MSK = timezone(timedelta(hours=3))
DAY = 86400
METRICS = ("buyers", "payments", "new_first_sql", "cohort_users", "cohort_buyers", "payments_with_new_first")


def _ts(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=MSK).timestamp())


class AmocrmMirrorAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, path=None, clock=datetime.now):
        self.section = section
        self.cfg = cfg
        self.path = Path(path) if path is not None else Path(section["path_root"]) / section["path"]
        self.clock = clock
        self._by_company = {spec["company"]: name for name, spec in section["scopes"].items()}
        self._by_pipeline = {int(pipeline): name for pipeline, name in section["pipeline_fallback"].items()}

    def _connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise SourceError(f"зеркало amoCRM не найдено: {self.path.name}")
        return sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)

    def _scopes(self, scope: str) -> list[str]:
        parts = sorted(scope.split("+"))
        unknown = [part for part in parts if part not in self.section["scopes"]]
        if unknown:
            raise GuardViolation(9, f"кабинеты {unknown} не объявлены для зеркала в конфигурации")
        return parts

    @staticmethod
    def _last_sync(conn: sqlite3.Connection) -> str | None:
        row = conn.execute("select value from meta where key = 'last_sync'").fetchone()
        return row[0] if row else None

    def _check_sync(self, conn: sqlite3.Connection, as_of: date) -> None:
        last = self._last_sync(conn)
        synced = datetime.fromisoformat(last).date() if last else None
        if synced != as_of:
            when = f"{synced:%d.%m.%Y}" if synced else "неизвестно когда"
            raise GuardViolation(13, f"зеркало синхронизировано {when}, а дата съёма прогона {as_of:%d.%m.%Y}: "
                                     f"синхронизируйте зеркало ({self.section['sync_command']}) — один прогон, "
                                     "один день (П3)", GuardViolation.COVERAGE)

    def _brand(self, pipeline, company) -> tuple[str | None, bool]:
        """Бренд сделки и признак «определён по воронке»."""
        if company:
            return self._by_company.get(company), False
        return self._by_pipeline.get(pipeline), True

    @staticmethod
    def _notes(unmatched: int, fallback: int, repeated: int = 0) -> list[str]:
        notes = []
        if unmatched:
            notes.append(f"сделок без бренда в конфигурации: {unmatched}")
        if repeated:
            notes.append(f"контактов с повторным флагом: {repeated}")
        if fallback:
            notes.append(f"бренд по воронке: {fallback}")
        return notes

    @staticmethod
    def _horizon(days, what: str) -> int:
        if days is None:
            raise GuardViolation(9, f"{what} не объявлен: economy.cohort_horizon_days в конфигурации")
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise GuardViolation(9, f"{what} = {days!r}: нужно целое число дней больше нуля")
        return days

    def _linked(self, first_flag, brand: str, closed_at: int) -> bool:
        """Оплата связана с New First, если первый флаг контакта того же бренда и поставлен не позже оплаты."""
        if first_flag is None:
            return False
        created_at, pipeline, company = first_flag
        return created_at <= closed_at and self._brand(pipeline, company)[0] == brand

    def fetch(self, query: Query) -> list[Number]:
        counters = {"new_first_sql": self._new_first, "cohort_users": self._cohort_users,
                    "cohort_buyers": self._cohort_buyers}
        return [self._number(query, counters.get(query.metric, self._paid))]

    def changed_since(self, query: Query, since: date) -> list[int]:
        """Номера оплаченных сделок окна — та же выборка, что у payments и buyers, — изменённых в CRM начиная с `since`:
        объясняют сдвиг известного ответа. Номер сделки — не ПДн. Сделки, ушедшие из окна, этим списком не видны."""
        if query.metric not in ("payments", "buyers"):
            raise GuardViolation(9, f"номера изменённых сделок отдаются для payments и buyers, а не «{query.metric}»")
        scopes = self._scopes(query.scope)
        pipelines = list(self.section["sales_pipelines"])
        sql = ("select l.lead_id, l.pipeline_id, "
               "(select f.value from lead_fields f where f.lead_id = l.lead_id and f.field_id = ? limit 1) "
               "from leads l where l.status_id = ? and l.contact_id is not null "
               f"and l.pipeline_id in ({', '.join('?' * len(pipelines))}) "
               "and l.closed_at >= ? and l.closed_at < ? and l.updated_at >= ? order by l.lead_id")
        params = [self.section["fields"]["company"], self.section["won_status"], *pipelines,
                  _ts(query.period_start), _ts(query.period_end), _ts(since)]
        with closing(self._connect()) as conn:
            self._check_sync(conn, query.as_of)
            rows = conn.execute(sql, params).fetchall()
        return [lead for lead, pipeline, company in rows if self._brand(pipeline, company)[0] in scopes]

    def cohort_buyers(self, query: Query, horizon_days: int) -> Number:
        """Покупатели когорты при явном горизонте — для калибровки горизонта по циклу сделки."""
        if query.metric != "cohort_buyers":
            raise GuardViolation(9, f"явный горизонт задаётся только для cohort_buyers, а не «{query.metric}»")
        return self._number(query, lambda conn, q, scopes: self._cohort_buyers(conn, q, scopes, horizon_days))

    def _number(self, query: Query, count) -> Number:
        if query.breakdown is not None:
            raise NotImplementedError("разрез зеркала пока не поддержан")
        if query.metric not in METRICS:
            raise GuardViolation(9, f"метрика «{query.metric}» из зеркала не отдаётся")
        if query.flow != "all":
            raise GuardViolation(9, "поток по визиту в CRM не определяется — зеркало отдаёт только all")
        scopes = self._scopes(query.scope)
        with closing(self._connect()) as conn:
            self._check_sync(conn, query.as_of)
            value, notes, weak = count(conn, query, scopes)
        return Number(metric=query.metric, level=self.cfg.rule(query.metric).level, scope="+".join(scopes),
                      flow="all", period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      source=f"D:{SYSTEM}:leads", status=Status.ESTIMATE if weak else Status.FACT,
                      value=float(value), missing="; ".join(notes))

    def _paid(self, conn: sqlite3.Connection, query: Query, scopes: list[str]):
        """Оплаченные сделки воронок продаж по дате закрытия; покупатели — уникальные контакты этих сделок;
        payments_with_new_first — оплаты, чей контакт получил флаг New First не позже оплаты."""
        pipelines = list(self.section["sales_pipelines"])
        sql = ("select l.pipeline_id, l.contact_id, l.closed_at, "
               "(select f.value from lead_fields f where f.lead_id = l.lead_id and f.field_id = ? limit 1) "
               "from leads l where l.status_id = ? and l.contact_id is not null "
               f"and l.pipeline_id in ({', '.join('?' * len(pipelines))}) "
               "and l.closed_at >= ? and l.closed_at < ?")
        params = [self.section["fields"]["company"], self.section["won_status"], *pipelines,
                  _ts(query.period_start), _ts(query.period_end)]
        first = self._first_flags(conn) if query.metric == "payments_with_new_first" else None
        deals, contacts, unmatched, fallback = 0, set(), 0, 0
        for pipeline, contact, closed_at, company in conn.execute(sql, params).fetchall():
            brand, by_pipeline = self._brand(pipeline, company)
            if brand is None:
                unmatched += 1
            elif brand in scopes:
                if first is not None and not self._linked(first.get(contact), brand, closed_at):
                    continue
                deals += 1
                contacts.add(contact)
                fallback += by_pipeline
        value = len(contacts) if query.metric == "buyers" else deals
        return value, self._notes(unmatched, fallback), bool(unmatched)

    def _new_first(self, conn: sqlite3.Connection, query: Query, scopes: list[str]):
        """Сделки с флагом New First по дате создания — так их считает Roistat. Второй флаг у контакта — дефект
        реестра: сделка остаётся в счёте, статус снижается до «оценки»."""
        flag = (self.section["fields"]["new_first_sql"], self.section["new_first_flag_value"])
        period = (_ts(query.period_start), _ts(query.period_end))
        flagged = "join lead_fields nf on nf.lead_id = l.lead_id and nf.field_id = ? and nf.value = ?"
        window = ("select l.pipeline_id, l.contact_id, "
                  "(select f.value from lead_fields f where f.lead_id = l.lead_id and f.field_id = ? limit 1) "
                  f"from leads l {flagged} where l.created_at >= ? and l.created_at < ?")
        rows = conn.execute(window, (self.section["fields"]["company"], *flag, *period)).fetchall()
        repeated_sql = (f"select l.contact_id from leads l {flagged} "
                        "where l.contact_id in (select l.contact_id from leads l "
                        f"{flagged} where l.created_at >= ? and l.created_at < ?) "
                        "group by l.contact_id having count(*) > 1")
        repeated_all = {row[0] for row in conn.execute(repeated_sql, (*flag, *flag, *period))}
        deals, contacts, unmatched, fallback = 0, set(), 0, 0
        for pipeline, contact, company in rows:
            brand, by_pipeline = self._brand(pipeline, company)
            if brand is None:
                unmatched += 1
            elif brand in scopes:
                deals += 1
                fallback += by_pipeline
                if contact is not None:
                    contacts.add(contact)
        repeated = len(contacts & repeated_all)
        return deals, self._notes(unmatched, fallback, repeated), bool(unmatched or repeated)

    def _first_flags(self, conn: sqlite3.Connection) -> dict:
        """Контакт → (дата создания, воронка, «Компания») его первой сделки с флагом New First."""
        flag = (self.section["fields"]["new_first_sql"], self.section["new_first_flag_value"])
        sql = ("select l.contact_id, l.created_at, l.pipeline_id, "
               "(select f.value from lead_fields f where f.lead_id = l.lead_id and f.field_id = ? limit 1) "
               "from leads l join lead_fields nf on nf.lead_id = l.lead_id and nf.field_id = ? and nf.value = ? "
               "where l.contact_id is not null and l.created_at is not null order by l.created_at, l.lead_id")
        first = {}
        for contact, created_at, pipeline, company in conn.execute(sql, (self.section["fields"]["company"], *flag)):
            first.setdefault(contact, (created_at, pipeline, company))
        return first

    def _won_closings(self, conn: sqlite3.Connection) -> dict:
        """Контакт → (дата закрытия, бренд) его оплаченных сделок воронок продаж по возрастанию даты."""
        pipelines = list(self.section["sales_pipelines"])
        sql = ("select l.contact_id, l.closed_at, l.pipeline_id, "
               "(select f.value from lead_fields f where f.lead_id = l.lead_id and f.field_id = ? limit 1) "
               "from leads l where l.status_id = ? and l.contact_id is not null and l.closed_at is not null "
               f"and l.pipeline_id in ({', '.join('?' * len(pipelines))}) order by l.closed_at")
        params = (self.section["fields"]["company"], self.section["won_status"], *pipelines)
        closings = {}
        for contact, closed_at, pipeline, company in conn.execute(sql, params):
            closings.setdefault(contact, []).append((closed_at, self._brand(pipeline, company)[0]))
        return closings

    def _cohort(self, conn: sqlite3.Connection, start: date, end: date, scopes: list[str]):
        """Контакт когорты → (дата New First, бренд); плюс число сделок без бренда и с брендом по воронке."""
        low, high = _ts(start), _ts(end)
        members, unmatched, fallback = {}, 0, 0
        for contact, (created_at, pipeline, company) in self._first_flags(conn).items():
            if not low <= created_at < high:
                continue
            brand, by_pipeline = self._brand(pipeline, company)
            if brand is None:
                unmatched += 1
            elif brand in scopes:
                members[contact] = (created_at, brand)
                fallback += by_pipeline
        return members, unmatched, fallback

    def _cohort_users(self, conn: sqlite3.Connection, query: Query, scopes: list[str]):
        members, unmatched, fallback = self._cohort(conn, query.period_start, query.period_end, scopes)
        return len(members), self._notes(unmatched, fallback), bool(unmatched)

    def _cohort_buyers(self, conn: sqlite3.Connection, query: Query, scopes: list[str], horizon_days=None):
        horizon = self._horizon(self.cfg.cohort_horizon_days if horizon_days is None else horizon_days,
                                "горизонт когорты")
        members, unmatched, fallback = self._cohort(conn, query.period_start, query.period_end, scopes)
        closings = self._won_closings(conn)
        buyers = before = other_brand = 0
        for contact, (new_first_at, brand) in members.items():
            deals = closings.get(contact, [])
            own = [t for t, deal_brand in deals if deal_brand == brand]
            if any(new_first_at <= t < new_first_at + horizon * DAY for t in own):
                buyers += 1
            elif any(t < new_first_at for t in own):
                before += 1
            elif any(new_first_at <= t < new_first_at + horizon * DAY for t, deal_brand in deals if deal_brand != brand):
                other_brand += 1
        notes = self._notes(unmatched, fallback)
        if before:
            notes.append(f"купили до New First: {before}")
        if other_brand:
            notes.append(f"купили в другом бренде: {other_brand}")
        matures_on = query.period_end + timedelta(days=horizon)
        young = matures_on > query.as_of
        if young:
            notes.append(f"когорта дозревает до {matures_on:%d.%m.%Y} (горизонт {horizon} дн.)")
        return buyers, notes, bool(unmatched or young)

    def cycle_days(self, scope: str, start: date, end: date, as_of: date, max_days: int) -> list[int]:
        """Дни от New First до первой оплаты того же бренда у покупателей когорты окна, не дольше max_days —
        агрегат без ПДн."""
        max_days = self._horizon(max_days, "предел цикла сделки")
        scopes = self._scopes(scope)
        with closing(self._connect()) as conn:
            self._check_sync(conn, as_of)
            members, _, _ = self._cohort(conn, start, end, scopes)
            closings = self._won_closings(conn)
        days = []
        for contact, (new_first_at, brand) in members.items():
            after = [t for t, deal_brand in closings.get(contact, []) if deal_brand == brand and t >= new_first_at]
            if after and after[0] - new_first_at < max_days * DAY:
                days.append((after[0] - new_first_at) // DAY)
        return sorted(days)

    def probe(self) -> list[ProbeResult]:
        try:
            with closing(self._connect()) as conn:
                last = self._last_sync(conn)
        except (SourceError, sqlite3.Error) as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        if not last:
            return [ProbeResult(SYSTEM, False, None, "в зеркале нет отметки синхронизации")]
        age = (self.clock() - datetime.fromisoformat(last)).days
        fresh = age <= self.section["freshness_max_days"]
        return [ProbeResult(SYSTEM, fresh, None, f"последняя синхронизация {last}, дней назад: {age}")]
