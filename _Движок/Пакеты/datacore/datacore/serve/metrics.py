"""Реестр метрик ядра: метрика определяется здесь один раз (Я12) и считается из facts, не из raw и не из отчётов систем."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from datacore.schema.errors import RuleViolation
from datacore.schema.number import Number, Status, add

import re

SOURCE_SYSTEM = "datacore"


def _rub(value: float) -> str:
    """Рубли с пробелом между тысячами. Отдельный помощник, потому что общая замена запятых по всей
    строке пояснения съедала знаки препинания в тексте («датой денег  а не датой отгрузки»).

    Пробел обычный, а не неразрывный: пояснение уходит в файл публикации и дальше в другие системы,
    где неразрывный пробел молча ломает сравнение строк."""
    return f"{value:,.0f}".replace(",", " ")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # имена кабинетов и систем, попадающие в текст запроса


def state_of(engine, systems: tuple[str, ...]) -> tuple[date | None, str]:
    """Состояние фактов: день последней успешной загрузки названных систем и пояснение, если после неё загрузка тех же
    систем не завершилась успешно — её частичные строки уже в фактах (ревью Codex этапа 2, 15.09.2026)."""
    if not systems:
        return None, ""
    marks = ", ".join("?" for _ in systems)
    # Дата съёма — день, на который успешна КАЖДАЯ обязательная система: иначе зеркало за 14-е и API за 13-е были бы
    # подписаны как единое состояние на 14-е (ревью Codex этапа 3, 15.09.2026).
    # Требуется успех каждой системы, которая уже участвует в фактах: если система ни разу не грузилась, её просто нет
    # в контуре инстанса, и ждать её нельзя; а вот отставание работающей системы занижает общую дату съёма.
    per_system = dict(engine.fetchall(f"SELECT source_system, MAX(as_of) FROM meta.load_log WHERE status = 'ok' "
                                      f"AND source_system IN ({marks}) GROUP BY source_system", systems))
    if not per_system:
        return None, ""
    state = min(per_system.values())
    behind = [f"{s} за {d:%d.%m.%Y}" for s, d in sorted(per_system.items()) if d != state]
    # Поминается только обрыв, случившийся ПОСЛЕ последней успешной загрузки своей системы: его частичные строки
    # могли остаться в фактах. Обрыв до успеха уже перекрыт — его строки перезаписаны, и вечно ссылаться на него
    # значит держать числа в статусе «оценка» без причины (живой случай 17.09.2026: у визитов 11 обрывов на
    # кабинет за живой сбор этапа 3, последняя загрузка успешна). У системы, где успеха не было ни разу, поминается
    # каждый обрыв: перекрывать их нечем (ревью Codex этапа 2 — обрыв API CRM при успешном зеркале).
    # Условие по дате применяется только к системам, у которых успех БЫЛ: у системы без единого успеха обрыв
    # любой давности актуален, и отсекать его общей датой — значит молчать о незагруженной системе
    # (ревью Codex этапа 5, п.4: API CRM с частичными строками за 10.09 при успешном зеркале за 17.09).
    unfinished = engine.fetchall(
        f"SELECT f.source_system, f.as_of, f.status FROM meta.load_log f "
        f"LEFT JOIN (SELECT source_system, MAX(started_at) AS ok_at FROM meta.load_log "
        f"           WHERE status = 'ok' GROUP BY source_system) o ON o.source_system = f.source_system "
        f"WHERE f.status <> 'ok' AND f.source_system IN ({marks}) AND f.as_of IS NOT NULL "
        f"AND (o.ok_at IS NULL OR (f.started_at > o.ok_at AND f.as_of >= ?)) "
        f"ORDER BY f.started_at",
        (*systems, state))
    # Одна строка на систему и день: за сутки неудачных прогонов бывает несколько, и повторять про каждый
    # одинаковый текст бессмысленно — важно, что они были, и сколько (16.09.2026).
    by_day: dict[tuple[str, date, str], int] = {}
    for sys_name, day, st in unfinished:
        by_day[(sys_name, day, st)] = by_day.get((sys_name, day, st), 0) + 1
    notes = [f"загрузка {sys_name} за {day:%d.%m.%Y} не завершена ({st}"
             + (f", таких прогонов {n}" if n > 1 else "")
             + ") — в фактах могут быть её частичные строки"
             for (sys_name, day, st), n in by_day.items()]
    if behind:
        notes.append("состояние по самой отстающей системе; свежее загружены: " + ", ".join(behind))
    return state, "; ".join(notes)


def last_as_of(engine, systems: tuple[str, ...]) -> date | None:
    """Дата съёма — день последней успешной загрузки названных систем (один прогон — один день)."""
    return state_of(engine, systems)[0]


def window(start: date, end: date, tz: str) -> tuple[datetime, datetime]:
    """Границы окна в поясе инстанса; правая граница исключающая."""
    z = ZoneInfo(tz)
    return datetime(start.year, start.month, start.day, tzinfo=z), datetime(end.year, end.month, end.day, tzinfo=z)


def coverage_note(engine, system: str, lo: datetime, hi: datetime) -> str:
    """Покрыт ли период загруженными окнами источника. Пустая строка — покрыт полностью; иначе пояснение, почему
    число не факт (ревью Codex этапа 3: ноль вне покрытия выдавался как факт).

    Считается по объединению окон, а не по общему минимуму и максимуму: две загрузки — за 1–5 и за 20–25 сентября —
    выглядели непрерывным покрытием 1–25, и выручка за 6–19 выдавалась как факт, хотя те дни не читались
    (ревью Codex этапа 4, п.2)."""
    rows = engine.fetchall("SELECT min_started_at, max_started_at FROM meta.window_log "
                           "WHERE source_system = ? AND min_started_at IS NOT NULL ORDER BY min_started_at", (system,))
    if not rows:
        return "загруженных окон источника нет — покрытие периода не доказано"
    # Покрытие считается по дням: первый визит дня бывает и в полдень, это не значит, что день не загружен.
    # Правая граница периода исключающая, поэтому последний нужный день — это день перед hi.
    need_from, need_to = lo.date(), (hi - timedelta(seconds=1)).date()
    day = need_from
    for first, last in rows:                     # окна отсортированы по началу
        first_day, last_day = first.date(), last.date()
        if first_day > day:
            break                                # дошли до окна, которое начинается позже нужного дня — разрыв
        if last_day >= day:
            day = last_day + timedelta(days=1)
            if day > need_to:
                return ""
    if day > need_to:
        return ""
    covered = f"{rows[0][0].date():%d.%m.%Y}", f"{max(r[1] for r in rows).date():%d.%m.%Y}"
    return (f"период не покрыт загрузкой: нет данных с {day:%d.%m.%Y}; "
            f"загружено с {covered[0]} по {covered[1]}, запрошено с {need_from:%d.%m.%Y} по {need_to:%d.%m.%Y}")


def from_snapshot(engine, metric: str, scope: str, start: date, end: date, as_of: date, segment: str = "",
                  **common) -> Number | None:
    """Число на прошлую дату — из снимка того дня, если он есть. Пересчитывать по сегодняшним фактам нельзя: источники
    дописывают данные задним числом, и подпись «на 09.09» под сегодняшним числом солгала бы (решение владельца 16.09.2026).

    Возвращается тот статус и то пояснение, с которыми число снято: «оценка» при чтении не становится «фактом».
    Снимок старого формата (до версии схемы 13) статуса не хранит — он возвращается «оценкой» с причиной."""
    from datacore.serve.snapshots import LEGACY_NOTE, read_snapshot
    snap = read_snapshot(engine, metric, scope, start, end, as_of, segment)
    if snap is None:
        return None
    notes = [f"снимок на {as_of:%d.%m.%Y}"]
    if snap["status"]:
        status = Status(snap["status"])
    else:
        status = Status.ESTIMATE
        notes.append(LEGACY_NOTE)
    if snap["missing"]:
        notes.append(snap["missing"])
    if snap["denominator_value"] is not None:
        common = {**common, "denominator_value": snap["denominator_value"]}
    return Number(metric=metric, scope=scope, period_start=start, period_end=end, segment=segment, as_of=as_of,
                  status=status, value=snap["value"], missing="; ".join(notes), **common)


def _checked_state(engine, systems: tuple[str, ...], as_of: date | None, what: str) -> tuple[date, str]:
    """Дата съёма фактов названных систем. Факты хранят одно, текущее состояние: число на другую дату съёма из них не
    строится, иначе подпись солжёт (К2)."""
    state, note = state_of(engine, systems)
    if state is None:
        raise RuleViolation("К2", f"в журнале загрузок нет успешной загрузки {what} — дата съёма неизвестна")
    if as_of is not None and as_of != state:
        raise RuleViolation("К2", f"факты {what} хранят состояние на {state:%d.%m.%Y}; число на дату съёма "
                                  f"{as_of:%d.%m.%Y} из них не строится — нужен снимок той даты")
    return state, note


def _crm_systems(cfg) -> tuple[str, ...]:
    return (cfg.crm["system_mirror"], cfg.crm["system_api"]) if cfg.crm else ()


def _own_marker_join(cfg) -> str:
    """Присоединение маркера заказа из кабинета своего бренда.

    У сделки по маркеру от каждого кабинета — без этого условия она посчиталась бы дважды (проверено на фактах
    16.09.2026: 630 сделок дают 1 260 маркеров). Имена систем берутся из конфигурации, в коде их нет (К9)."""
    by_scope = (cfg.tracking or {}).get("system_by_scope", {})
    if not by_scope:
        return ""
    # Значения идут в текст запроса, поэтому проверяются по форме: имя кабинета и системы — латиница, цифры,
    # дефис и подчёркивание. Кавычка в конфигурации ломала запрос, а при недоверенной конфигурации открывала
    # путь к произвольному SQL (ревью Codex этапа 5, п.3).
    for scope, system in by_scope.items():
        for name in (scope, system):
            if not _SAFE_NAME.match(str(name)):
                raise RuleViolation("К2", f"имя «{name}» в system_by_scope не похоже на имя кабинета или системы")
    whens = " ".join(f"WHEN '{scope}' THEN '{system}'" for scope, system in sorted(by_scope.items()))
    return ("LEFT JOIN facts.order_marker m ON m.deal_id = d.deal_id "
            f"AND m.source_system = CASE d.brand {whens} END")


def new_first_sql(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                  contour: str = "full", segment: str = "") -> Number:
    """Сделки CRM по правилам набора отбора: дата создания в окне, без удалённых и исключённых.

    `contour` — имя набора из конфигурации (Я12). Одна модель даёт все числа: `all_deals` — все сделки компании
    (август 2026: 2 893), `full` — только новые первые квалифицированные (630), `vitrina` / `vitrina_pik` /
    `digital` — они же с фильтрами контура (591 / 570 / 545 по основному кабинету). Умолчание `full` — прежнее
    поведение метрики."""
    from datacore.serve.contour import contour_base, contour_clause, contour_needs_visit
    if scope not in cfg.scopes:
        raise RuleViolation("К2", f"кабинет «{scope}» не объявлен в конфигурации: {cfg.scopes}")
    # Набор приходит либо параметром, либо разрезом «contour=имя»: проверка известных ответов и команда ask
    # передают именно segment. Другого разреза у этой метрики нет — иначе чужой разрез посчитался бы молча.
    if segment:
        if not segment.startswith(CONTOUR_SEGMENT):
            raise RuleViolation("К2", f"у этой метрики разрез только «{CONTOUR_SEGMENT}<набор>», получено «{segment}»")
        contour = segment[len(CONTOUR_SEGMENT):]
    segment = "" if contour == "full" else f"{CONTOUR_SEGMENT}{contour}"
    clause, clause_params = contour_clause(cfg, contour)     # К2, если набора нет — до обращения к базе
    if as_of is not None:
        snap = from_snapshot(engine, "new_first_sql", scope, start, end, as_of, segment, level="new_first_sql",
                             flow="all", unit="шт", source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
        if snap is not None:
            return snap
    as_of, state_note = _checked_state(engine, _crm_systems(cfg), as_of, "CRM")
    lo, hi = window(start, end, cfg.timezone)
    join = _own_marker_join(cfg)
    # Факты визитов присоединяются, только если набор спрашивает про площадку перехода: лишнее соединение
    # на 2,6 млн строк замедлило бы каждое число. Связь один к одному — у маркера один визит.
    if contour_needs_visit(cfg, contour):
        join += (" LEFT JOIN facts.visit v ON v.visit_id = m.visit_id "
                 "AND v.source_system = m.source_system")
    where = " AND " + clause if clause else ""
    # is_new_first здесь больше нет: «только первые квалифицированные» — база набора, она приходит в clause.
    # Удалённые и технические тесты остаются всегда: удалённая сделка не факт, тестовая — не сделка (задача 0).
    rows = engine.fetchall(
        f"SELECT d.brand, COUNT(DISTINCT d.deal_id) FROM facts.deal d {join} WHERE d.deleted_at IS NULL "
        "AND d.created_at >= ? AND d.created_at < ? AND NOT EXISTS (SELECT 1 FROM facts.exclusion e "
        f"WHERE e.entity = 'deal' AND e.entity_key = CAST(d.deal_id AS VARCHAR)){where} GROUP BY d.brand",
        (lo, hi, *clause_params))
    counts = {brand: int(n) for brand, n in rows}
    unknown_brand = cfg.crm["brand"]["unknown"] if cfg.crm else "unknown"
    notes = [state_note] if state_note else []
    # Что метрика отсекла сама. Эти два условия остаются всегда — удалённая сделка не факт, тестовая не сделка, —
    # но обязаны быть названы: иначе число выглядит полным, хотя таковым не является (решение владельца 17.09.2026).
    if not contour_base(cfg, contour).get("new_first_only"):
        notes.append("все сделки набора — не только первые квалифицированные")
    brand_cond = "" if scope == "company" else " AND d.brand = ?"
    brand_args = () if scope == "company" else (scope,)
    # Счёт отсечённого идёт по тому же набору и тем же условиям, что и основной запрос: иначе пояснение говорит
    # о сделках, которых в этом контуре не было бы в любом случае (ревью Codex этапа 5, п.5). Причины
    # взаимоисключающие: сначала удаление, потом технический тест — одна сделка не попадает в оба счётчика.
    deleted_n, excluded_n = engine.fetchone(
        f"SELECT COUNT(DISTINCT d.deal_id) FILTER (WHERE d.deleted_at IS NOT NULL), "
        "COUNT(DISTINCT d.deal_id) FILTER (WHERE d.deleted_at IS NULL AND EXISTS "
        "(SELECT 1 FROM facts.exclusion e WHERE e.entity = 'deal' "
        f"AND e.entity_key = CAST(d.deal_id AS VARCHAR))) FROM facts.deal d {join} "
        f"WHERE d.created_at >= ? AND d.created_at < ?{brand_cond}{where}",
        (lo, hi, *brand_args, *clause_params))
    # Штатный отбор называется, но статус не понижает: удалённая в CRM сделка и технический тест отсекаются
    # намеренно — они делают число правильным, а не неполным. Статус «оценка» бережём для настоящей
    # неопределённости (незавершённая загрузка, непокрытый период, неизвестный бренд), иначе он обесценится.
    routine: list[str] = []
    if deleted_n:
        routine.append(f"удалённых в CRM сделок в окне: {deleted_n} — в число не вошли")
    if excluded_n:
        routine.append(f"исключённых (технические тесты): {excluded_n} — в число не вошли")
    # Бренд бывает и пустым (NULL), и строкой-заглушкой из конфигурации — обе формы означают «не определён»
    # и обязаны понижать статус: разложить такое число по кабинетам нельзя (ревью Codex этапа 5, п.6).
    unknown = sum(n for b, n in counts.items() if b is None or b == unknown_brand)
    if unknown:
        notes.append(f"сделок с неопределённым брендом в окне: {unknown} — по кабинетам не раскладываются")
    outside = {b: n for b, n in counts.items() if b is not None and b != unknown_brand and b not in cfg.scopes}
    if outside:                                        # бренд вне кабинетов конфигурации не выпадает молча
        notes.append("бренды вне кабинетов конфигурации: "
                     + ", ".join(f"{b} {n}" for b, n in sorted(outside.items(), key=lambda x: str(x[0]))))
    status = Status.ESTIMATE if notes else Status.FACT      # статус — только по неопределённости
    missing = "; ".join(notes + routine)                    # пояснение — по всему, что не вошло в число
    common = dict(metric="new_first_sql", level="new_first_sql", flow="all", period_start=start, period_end=end,
                  source=f"D:{SOURCE_SYSTEM}:facts.deal is_new_first", as_of=as_of, missing=missing, segment=segment)
    if scope != "company":
        return Number(scope=scope, status=status, value=float(counts.get(scope, 0)), **common)
    # Компания — это все её сделки, а не сумма объявленных кабинетов: у 4 сделок августа 2026 бренд не определён,
    # и сложение кабинетов давало 2 889 вместо 2 893. Сколько их и каких — уже сказано в пояснении выше.
    # Та же ошибка была в выручке этапа 4 (там выпадало 19,8 млн ₽) — здесь она повторилась в другой метрике.
    if "company" not in cfg.scopes:
        raise RuleViolation("К2", f"кабинет «company» не объявлен в конфигурации: {cfg.scopes}")
    return Number(scope="company", status=status, value=float(sum(counts.values())), **common)


CONTOUR_SEGMENT = "contour="            # разрез метрики по набору отбора (этап 5)
CALLS_WITH_VISIT = "visit=linked"          # разрез звонков со связанным визитом (формат разреза — «измерение=значение»)
CHANNEL_GROUP = "channel_group="           # разрез New First SQL по своей группе каналов
EMPTY_GROUP = "(пусто)"                    # группа каналов без значения (прямые визиты) — пустая строка в фактах


def _tracking_system(cfg, scope: str) -> str:
    systems = (cfg.tracking or {}).get("system_by_scope", {})
    if scope not in systems:
        raise RuleViolation("К2", f"у кабинета «{scope}» нет системы визитов в конфигурации")
    return systems[scope]


def _per_scope(cfg, metric: str, scope: str, one) -> Number:
    if scope not in cfg.scopes:
        raise RuleViolation("К2", f"кабинет «{scope}» не объявлен в конфигурации: {cfg.scopes}")
    if scope != "company":
        return one(scope)
    proof = cfg.cross_scope_proof(metric)
    if proof is None:                      # отказ раньше расчёта частей: иначе вместо К7 всплыла бы чужая причина
        raise RuleViolation("К7", f"«{metric}» по кабинетам не складывается: доказательства сложения нет в конфигурации")
    return add([one(b) for b in cfg.scopes if b != "company"], cross_scope_proof=proof)


def visits(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None, segment: str = "") -> Number:
    """Визиты кабинета без групп каналов роботов, по дате визита в поясе инстанса."""
    if segment:
        raise RuleViolation("К2", f"у визитов нет разреза «{segment}»")
    bots = tuple((cfg.tracking or {}).get("bot_groups", []))
    lo, hi = window(start, end, cfg.timezone)

    def one(s):
        system = _tracking_system(cfg, s)
        if as_of is not None:
            snap = from_snapshot(engine, "visits", s, start, end, as_of, level="visit", flow="web", unit="шт",
                                 source=f"C:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (system,), as_of, "визитов")
        gap = coverage_note(engine, system, lo, hi)
        common = dict(metric="visits", level="visit", scope=s, flow="web", period_start=start, period_end=end,
                      source=f"C:{SOURCE_SYSTEM}:facts.visit", unit="шт", as_of=state)
        if gap:                                   # вне загруженного покрытия ноль — это «нет данных», а не факт
            return Number(status=Status.NO_DATA, value=None, missing="; ".join(x for x in (gap, note) if x), **common)
        robots = (" AND COALESCE(marker_level_1, '') NOT IN (" + ", ".join("?" for _ in bots) + ")") if bots else ""
        n = engine.fetchone(f"SELECT COUNT(*) FROM facts.visit WHERE source_system = ? AND started_at >= ? AND started_at < ?{robots}",
                            (system, lo, hi, *bots))[0]
        return Number(status=Status.ESTIMATE if note else Status.FACT, value=float(n), missing=note, **common)
    return _per_scope(cfg, "visits", scope, one)


def calls(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None, segment: str = "") -> Number:
    """Звонки кабинета по дате звонка; segment="visit=linked" — только со связанным визитом."""
    if segment not in ("", CALLS_WITH_VISIT):
        raise RuleViolation("К2", f"у звонков нет разреза «{segment}»")
    lo, hi = window(start, end, cfg.timezone)

    def one(s):
        system = _tracking_system(cfg, s)
        if as_of is not None:
            snap = from_snapshot(engine, "calls", s, start, end, as_of, segment=segment, level="lead", flow="all",
                                 unit="шт", source=f"C:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (system,), as_of, "звонков")
        # «Звонок с визитом» — визит того же кабинета существует в фактах, а не просто заполненный номер (ревью Codex).
        extra = (" AND EXISTS (SELECT 1 FROM facts.visit v WHERE v.visit_id = facts.phone_call.visit_id "
                 "AND v.source_system = facts.phone_call.source_system)") if segment else ""
        n = engine.fetchone(f"SELECT COUNT(*) FROM facts.phone_call WHERE source_system = ? AND started_at >= ? AND started_at < ?{extra}",
                            (system, lo, hi))[0]
        return Number(metric="calls", level="lead", scope=s, flow="all", period_start=start, period_end=end, segment=segment,
                      source=f"C:{SOURCE_SYSTEM}:facts.phone_call", status=Status.ESTIMATE if note else Status.FACT,
                      value=float(n), unit="шт", as_of=state, missing=note)
    return _per_scope(cfg, "calls", scope, one)


def new_first_sql_by_channel(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                             segment: str = "") -> Number:
    """New First SQL бренда по своей группе каналов: маркер заказа берётся из кабинета бренда сделки."""
    if not segment.startswith(CHANNEL_GROUP) or segment == CHANNEL_GROUP:
        raise RuleViolation("К2", f"разрез «{segment}»: нужна группа каналов «{CHANNEL_GROUP}<группа>»; число без разреза — new_first_sql")
    value = segment[len(CHANNEL_GROUP):]
    group = "" if value == EMPTY_GROUP else value
    lo, hi = window(start, end, cfg.timezone)

    def one(s):
        system = _tracking_system(cfg, s)
        if as_of is not None:
            snap = from_snapshot(engine, "new_first_sql_by_channel", s, start, end, as_of, segment=segment,
                                 level="new_first_sql", flow="all", unit="шт",
                                 source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        crm_state, crm_note = _checked_state(engine, _crm_systems(cfg), None, "CRM")
        tr_state, tr_note = _checked_state(engine, (system,), None, "маркеров заказов")
        state = min(crm_state, tr_state)
        if as_of is not None and as_of != state:
            raise RuleViolation("К2", f"факты CRM и маркеров хранят состояние на {state:%d.%m.%Y}; число на "
                                      f"{as_of:%d.%m.%Y} из них не строится")
        n, unknown = engine.fetchone(
            "SELECT COUNT(*) FILTER (WHERE om.marker_level_1 = ?), COUNT(*) FILTER (WHERE om.marker_level_1 IS NULL) "
            "FROM facts.deal d LEFT JOIN facts.order_marker om ON om.deal_id = d.deal_id AND om.source_system = ? "
            "WHERE d.brand = ? AND d.is_new_first AND d.deleted_at IS NULL AND d.created_at >= ? AND d.created_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM facts.exclusion e WHERE e.entity = 'deal' AND e.entity_key = CAST(d.deal_id AS VARCHAR))",
            (group, system, s, lo, hi))
        notes = [x for x in (crm_note, tr_note) if x]
        if crm_state != tr_state:
            notes.append(f"дата съёма CRM {crm_state:%d.%m.%Y}, маркеров {tr_state:%d.%m.%Y}")
        if unknown:
            notes.append(f"сделок без группы каналов (нет заказа или визита): {unknown}")
        return Number(metric="new_first_sql_by_channel", level="new_first_sql", scope=s, flow="all", period_start=start,
                      period_end=end, segment=segment, source=f"D:{SOURCE_SYSTEM}:facts.deal × facts.order_marker",
                      status=Status.ESTIMATE if notes else Status.FACT, value=float(n or 0), unit="шт", as_of=state,
                      missing="; ".join(notes))
    return _per_scope(cfg, "new_first_sql_by_channel", scope, one)


def _money_system(cfg) -> str:
    system = (cfg.money or {}).get("system")
    if not system:
        raise RuleViolation("К2", "в конфигурации нет системы денег — выручку считать не из чего")
    return system


def revenue(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None, segment: str = "") -> Number:
    """Выручка кабинета: сумма платежей по дате денег, а не по дате заказа. Платежи без сделки в кабинет не
    попадают — о них сказано в пояснении, чтобы разрыв с общей суммой не выглядел потерей."""
    if segment:
        raise RuleViolation("К2", f"у выручки нет разреза «{segment}»")
    system = _money_system(cfg)
    lo, hi = window(start, end, cfg.timezone)

    def one(s, all_brands: bool = False):
        if as_of is not None:
            snap = from_snapshot(engine, "revenue", s, start, end, as_of, "", level="payment", flow="all", unit="₽",
                                 source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (system,), as_of, "денег")
        gap = coverage_note(engine, system, lo, hi)
        common = dict(metric="revenue", level="payment", scope=s, flow="all", period_start=start, period_end=end,
                      source=f"D:{SOURCE_SYSTEM}:facts.payment", unit="₽", as_of=state)
        if gap:                                   # вне загруженного покрытия ноль рублей — это «нет данных», а не факт
            return Number(status=Status.NO_DATA, value=None, missing="; ".join(x for x in (gap, note) if x), **common)
        # Только платежи с документом-основанием: перевод эквайринга по реестру — это те же деньги, уже
        # посчитанные по покупкам клиентов (миграция 0007).
        # У компании считаются все её деньги, у кабинета — только его. Заказ без поля «Компания» принадлежит
        # компании, и складывая одни кабинеты, ядро занижало выручку на 19,8 млн ₽ за янв–июл 2026 (сверка 16.09.2026).
        brand_where = "" if all_brands else " AND brand = ?"
        args = (system, lo, hi) if all_brands else (system, s, lo, hi)
        total = engine.fetchone(f"SELECT COALESCE(SUM(revenue), 0) FROM facts.payment "
                                f"WHERE money_system = ?{brand_where} AND has_basis "
                                "AND paid_at >= ? AND paid_at < ?", args)[0]
        # Платежи и возвраты без сделки называются раздельно: сложенные вместе, они гасят друг друга, и пояснение
        # занижает обе величины (живой случай 16.09.2026 — все четыре возврата сентября пришли по розничным
        # отгрузкам без заказа).
        orphan_in, orphan_out = engine.fetchone(
            "SELECT COALESCE(SUM(CASE WHEN revenue > 0 THEN revenue END), 0), "
            "COALESCE(SUM(CASE WHEN revenue < 0 THEN -revenue END), 0) FROM facts.payment "
            "WHERE money_system = ? AND deal_id IS NULL AND has_basis AND paid_at >= ? AND paid_at < ?", (system, lo, hi))
        no_basis = engine.fetchone("SELECT COALESCE(SUM(revenue), 0) FROM facts.payment "
                                   "WHERE money_system = ? AND NOT has_basis AND paid_at >= ? AND paid_at < ?", (system, lo, hi))[0]
        # Статус понижает только неопределённость — незавершённая загрузка денег. Платежи без сделки, возвраты
        # без сделки, платежи без основания и деньги без бренда — объявленные отборы определения выручки (стандарт,
        # раздел 3а): они делают число верным, а не неполным, поэтому названы суммой, но статус не трогают.
        # «Оценка» из-за них держала бы выручку в оценке всегда и обесценила бы статус (ревью методологии 28.09.2026).
        notes = [note] if note else []
        routine: list[str] = []
        if orphan_in:
            routine.append(f"платежей без сделки на {_rub(float(orphan_in))} ₽ — в кабинет не отнесены")
        if orphan_out:
            routine.append(f"возвратов без сделки на {_rub(float(orphan_out))} ₽ — в кабинет не отнесены")
        if no_basis:                           # исключённые деньги названы, а не пропадают молча
            routine.append(f"платежей без документа-основания на {_rub(float(no_basis))} ₽ — не выручка по сделкам (перевод эквайринга по реестру)")
        if all_brands:
            nameless = engine.fetchone("SELECT COALESCE(SUM(revenue), 0) FROM facts.payment WHERE money_system = ? "
                                       "AND brand IS NULL AND has_basis AND paid_at >= ? AND paid_at < ?",
                                       (system, lo, hi))[0]
            if nameless:
                routine.append(f"в том числе без бренда {_rub(float(nameless))} ₽ — кабинет не определён")
        return Number(status=Status.ESTIMATE if notes else Status.FACT, value=float(total),
                      missing="; ".join(notes + routine), **common)
    if scope == "company":
        # Компания — это все её деньги, а не сумма кабинетов: сложения здесь нет, и К7 не применяется.
        if "company" not in cfg.scopes:
            raise RuleViolation("К2", f"кабинет «company» не объявлен в конфигурации: {cfg.scopes}")
        return one("company", all_brands=True)
    return _per_scope(cfg, "revenue", scope, one)


def payments(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None, segment: str = "") -> Number:
    """Оплаченные сделки: считаются сделки, а не документы — у одной сделки бывает несколько платежей."""
    if segment:
        raise RuleViolation("К2", f"у оплат нет разреза «{segment}»")
    system = _money_system(cfg)
    lo, hi = window(start, end, cfg.timezone)

    def one(s):
        if as_of is not None:
            snap = from_snapshot(engine, "payments", s, start, end, as_of, "", level="payment", flow="all", unit="шт",
                                 source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (system,), as_of, "денег")
        gap = coverage_note(engine, system, lo, hi)
        common = dict(metric="payments", level="payment", scope=s, flow="all", period_start=start, period_end=end,
                      source=f"D:{SOURCE_SYSTEM}:facts.payment", unit="шт", as_of=state)
        if gap:
            return Number(status=Status.NO_DATA, value=None, missing="; ".join(x for x in (gap, note) if x), **common)
        # Только положительные суммы: возврат покупателя лежит в тех же фактах отрицательным числом и
        # оплаченной сделкой не является.
        n = engine.fetchone("SELECT COUNT(DISTINCT deal_id) FROM facts.payment WHERE money_system = ? AND brand = ? "
                            "AND deal_id IS NOT NULL AND revenue > 0 AND has_basis "
                            "AND paid_at >= ? AND paid_at < ?", (system, s, lo, hi))[0]
        return Number(status=Status.ESTIMATE if note else Status.FACT, value=float(n), missing=note, **common)
    return _per_scope(cfg, "payments", scope, one)


def _cogs_of_paid_orders(engine, system: str, scope: str, lo: datetime, hi: datetime,
                         all_brands: bool) -> tuple[float, float, int, int, float]:
    """Себестоимость заказов, деньги по которым пришли в периоде.

    Отдаёт: себестоимость, продажи позиций без закупочной цены, число заказов с себестоимостью, число оплаченных
    заказов без неё и сумму оплат по ним.

    Период задаётся датой денег, а не датой отгрузки: иначе валовая прибыль вычиталась бы из выручки одного месяца
    себестоимостью другого. Заказ берётся один раз, сколько бы платежей по нему ни прошло, — иначе себестоимость
    задвоится на авансе и доплате.

    Кабинет берётся у платежа, а не у отгрузки: у заказа вне выборки бренда нет, но деньги по нему отнесены
    кабинетом платежа, и себестоимость должна идти туда же.

    Оплаченные заказы без отгрузки считаются отдельно и называются вслух: их себестоимость неизвестна, и молча
    вычесть из выручки себестоимость только части проданного значит завысить валовую прибыль (аванс за товар,
    который уедет клиенту в следующем месяце, — обычное дело)."""
    brand_where = "" if all_brands else " AND p.brand = ?"
    paid = ("SELECT p.order_id, SUM(p.revenue) AS paid FROM facts.payment p "
            f"WHERE p.money_system = ?{brand_where} AND p.order_id IS NOT NULL AND p.has_basis "
            "  AND p.revenue > 0 AND p.paid_at >= ? AND p.paid_at < ? GROUP BY p.order_id")
    # Отгрузка связана с деньгами заказом или счётом; и то и другое ведёт к одному заказу. Себестоимость
    # складывается по заказу: у одного заказа бывает несколько отгрузок (частями), и все они — одна продажа.
    #
    # `outside` — часть себестоимости, отгруженная вне периода. Заказ оплачен в августе, а уехал в июле или
    # сентябре: его себестоимость всё равно вся относится к этой продаже. Живая доля 21.09.2026 — 23,6 % числа
    # августа, и молчать о ней нельзя: читатель принял бы число за «затраты августа».
    per_order = ("SELECT order_id, SUM(cogs) AS cogs, SUM(unpriced_revenue) AS unpriced, "
                 "       SUM(CASE WHEN shipped_at >= ? AND shipped_at < ? THEN 0 ELSE cogs END) AS outside "
                 "FROM facts.shipment_cogs WHERE money_system = ? AND order_id IS NOT NULL GROUP BY order_id")
    args = (system, lo, hi) if all_brands else (system, scope, lo, hi)
    row = engine.fetchone(
        "SELECT COALESCE(SUM(c.cogs), 0), COALESCE(SUM(c.unpriced), 0), "
        "COUNT(c.order_id), COUNT(*) - COUNT(c.order_id), "
        "COALESCE(SUM(CASE WHEN c.order_id IS NULL THEN o.paid END), 0), "
        "COALESCE(SUM(c.outside), 0) "
        f"FROM ({paid}) o LEFT JOIN ({per_order}) c ON c.order_id = o.order_id", (*args, lo, hi, system))
    return float(row[0]), float(row[1]), int(row[2]), int(row[3]), float(row[4]), float(row[5])


def cogs(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None, segment: str = "") -> Number:
    """Себестоимость проданного по заказам, оплаченным в периоде.

    Считается по строкам отгрузки: закупочная цена позиции × количество. Правило подтверждено владельцем
    финансового контура 20.09.2026. Позиции без закупочной цены не обнуляются — их продажа названа в пояснении, и
    число становится оценкой: без этого валовая прибыль была бы завышена молча.

    ⚠️ Закупочная цена в справочнике одна — текущая, снимка на прошлый месяц там нет. Поэтому себестоимость всегда
    подписана датой съёма, и пересчёт давнего месяца позже может дать другое число. Это свойство источника."""
    if segment:
        raise RuleViolation("К2", f"у себестоимости нет разреза «{segment}»")
    system = _money_system(cfg)
    lo, hi = window(start, end, cfg.timezone)

    def one(s, all_brands: bool = False):
        if as_of is not None:
            snap = from_snapshot(engine, "cogs", s, start, end, as_of, "", level="payment", flow="all", unit="₽",
                                 source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (system,), as_of, "денег")
        # Покрытие проверяется у СЕБЕСТОИМОСТИ, а не у денег: справочник и отгрузки читаются отдельно и могут
        # быть недоступны при успешно прочитанных платежах. Без этого метрика выдавала бы старые строки за
        # свежий факт (ревью Codex этапа 5, п.3).
        gap = coverage_note(engine, f"{system}-cogs", lo, hi)
        common = dict(metric="cogs", level="payment", scope=s, flow="all", period_start=start, period_end=end,
                      source=f"D:{SOURCE_SYSTEM}:facts.shipment_cogs", unit="₽", as_of=state)
        if gap:
            return Number(status=Status.NO_DATA, value=None, missing="; ".join(x for x in (gap, note) if x), **common)
        total, unpriced, shipped, unshipped, unshipped_paid, outside = _cogs_of_paid_orders(
            engine, system, s, lo, hi, all_brands)
        if not shipped:
            # Оплаты есть, а отгрузок по ним нет: себестоимость неизвестна, и ноль рублей здесь был бы ложью —
            # он означал бы «продано даром», а не «ещё не отгружено».
            return Number(status=Status.NO_DATA, value=None,
                          missing="; ".join(x for x in ("по оплаченным заказам периода нет отгрузок — "
                                                        "себестоимость неизвестна", note) if x), **common)
        notes = [note] if note else []
        if unpriced:
            notes.append(f"позиций без закупочной цены на {_rub(unpriced)} ₽ продаж — "
                         f"их себестоимость не учтена")
        if unshipped:
            # Без этого пояснения валовая прибыль была бы завышена молча: из выручки вычлась бы себестоимость
            # только части проданного. Аванс за товар, который уедет в следующем месяце, — обычное дело.
            # Доля названа вместе со штуками: 3 заказа из 400 и 200 из 400 — разная мера доверия к числу.
            share = f", это {unshipped / (shipped + unshipped) * 100:.0f} % оплаченных заказов периода"
            notes.append(f"оплаченных заказов без отгрузки {unshipped} на {_rub(unshipped_paid)} ₽{share} — "
                         f"их себестоимость ещё неизвестна")
        if outside and total:
            # Период задан датой денег, а отгрузка живёт своей датой: заказ оплачен в августе, а уехал в июле.
            # Без этой строки число читалось бы как «затраты месяца», хотя это себестоимость оплаченного.
            # Разделители тысяч чистятся только в самом числе: общая замена по строке съедала запятые в тексте.
            notes.append(f"из них отгружено вне периода {_rub(outside)} ₽ ({outside / total * 100:.0f} %) — "
                         f"период задан датой денег, а не датой отгрузки")
        return Number(status=Status.ESTIMATE if notes else Status.FACT, value=total,
                      missing="; ".join(notes), **common)
    if scope == "company":
        if "company" not in cfg.scopes:
            raise RuleViolation("К2", f"кабинет «company» не объявлен в конфигурации: {cfg.scopes}")
        return one("company", all_brands=True)
    return _per_scope(cfg, "cogs", scope, one)


def gross_profit(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                 segment: str = "") -> Number:
    """Валовая прибыль: выручка минус себестоимость проданного.

    Обе части берутся за один период и по одному правилу отбора, иначе разность не имеет смысла. Если хоть одна
    часть не факт, разность тоже не факт: вычесть оценку из факта и назвать результат фактом нельзя."""
    if segment:
        raise RuleViolation("К2", f"у валовой прибыли нет разреза «{segment}»")
    rev = revenue(engine, cfg, scope, start, end, as_of)
    cost = cogs(engine, cfg, scope, start, end, as_of)
    common = dict(metric="gross_profit", level="payment", scope=scope, flow="all", period_start=start,
                  period_end=end, source=f"D:{SOURCE_SYSTEM}:facts.payment − facts.shipment_cogs", unit="₽",
                  as_of=rev.as_of)
    if rev.value is None or cost.value is None:
        why = [n.missing for n in (rev, cost) if n.value is None and n.missing]
        return Number(status=Status.NO_DATA, value=None, missing="; ".join(why), **common)
    notes = [n.missing for n in (rev, cost) if n.missing]
    worst = Status.FACT if rev.status is Status.FACT and cost.status is Status.FACT else Status.ESTIMATE
    return Number(status=worst, value=rev.value - cost.value, missing="; ".join(notes), **common)


def ampu(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None, segment: str = "") -> Number:
    """Средняя валовая прибыль на оплаченную сделку (AMPU).

    Знаменатель — оплаченные сделки того же периода, то есть сделки, по которым пришли деньги. Доля обязана иметь
    знаменатель, и здесь он не просто назван, а проверен: ноль сделок при ненулевой прибыли — это не бесконечность,
    а признак того, что деньги пришли по заказам без сделки CRM."""
    if segment:
        raise RuleViolation("К2", f"у средней прибыли на сделку нет разреза «{segment}»")
    profit = gross_profit(engine, cfg, scope, start, end, as_of)
    deals = payments(engine, cfg, scope, start, end, as_of)
    common = dict(metric="ampu", level="deal", scope=scope, flow="all", period_start=start, period_end=end,
                  source=f"D:{SOURCE_SYSTEM}:gross_profit ÷ payments", unit="₽",
                  denominator="оплаченных сделок", as_of=profit.as_of)
    if profit.value is None or deals.value is None:
        why = [n.missing for n in (profit, deals) if n.value is None and n.missing]
        return Number(status=Status.NO_DATA, value=None, missing="; ".join(why), **common)
    if not deals.value:
        return Number(status=Status.NO_DATA, value=None, denominator_value=0.0,
                      missing="оплаченных сделок в периоде нет — делить не на что", **common)
    notes = [n.missing for n in (profit, deals) if n.missing]
    worst = Status.FACT if profit.status is Status.FACT and deals.status is Status.FACT else Status.ESTIMATE
    return Number(status=worst, value=profit.value / deals.value, denominator_value=deals.value,
                  missing="; ".join(notes), **common)


# Когорта лидов: клиенты, пришедшие заявкой в периоде, и деньги по ним.
#
# ⚠️ Заявка и продажа — РАЗНЫЕ сделки в разных воронках. Флаг «новая первая квалифицированная» ставится в воронке
# первичных обращений, а деньги приходят по сделкам воронки продаж: из 1 164 оплаченных сделок июн–авг 2026 флаг
# есть у четырёх. Связывает их контакт — у 851 клиента из 942 (90 %) есть и заявка с флагом, и оплаченная сделка.
# Деление «оплаченные сделки ÷ заявки» без этого перехода давало конверсию 59 % вместо 24,4 % (21.09.2026):
# числитель и знаменатель брались из разных воронок, и ошибка выглядела хорошим результатом.
#
# ⚠️ Это ВЕРХНЯЯ оценка: клиент мог заплатить по заявке прошлого периода. Для когорты в строгом смысле нужна дата
# заявки у самой оплаты; такого поля в фактах нет, и метрика говорит об этом вслух.
_COHORT_SQL = """
WITH leads AS (
  SELECT DISTINCT d.contact_id FROM facts.deal d
  WHERE d.is_new_first AND d.deleted_at IS NULL AND d.contact_id IS NOT NULL AND d.brand = ?
    AND d.created_at >= ? AND d.created_at < ?
    AND NOT EXISTS (SELECT 1 FROM facts.exclusion e WHERE e.entity = 'deal'
                    AND e.entity_key = CAST(d.deal_id AS VARCHAR)){channel}
),
-- Одна строка на ЗАКАЗ, а не на платёж: иначе себестоимость заказа присоединилась бы столько раз, сколько по
-- нему прошло платежей, и аванс с доплатой задваивали бы её (тест поймал 120 000 ₽ вместо 60 000 ₽).
-- Платёж отбирается один раз по своему номеру: одна сделка бывает связана с несколькими лидами-контактами,
-- и без DISTINCT по платежу его сумма вошла бы в выручку дважды.
paid AS (
  SELECT order_id, SUM(revenue) AS revenue FROM (
    -- Кабинет платежа проверяется наравне с кабинетом заявки: один клиент бывает и там и там, и без этого
    -- условия деньги второго кабинета попадали в когорту первого. Живая проверка 21.09.2026 (ревью Codex, п.1):
    -- у кабинета поменьше это 441 400 из 7,2 млн рублей, около шестнадцатой части его выручки; при сложении
    -- кабинетов платёж считался бы дважды.
    -- ⚠️ Знак процента в комментарии SQL недопустим: драйвер Postgres читает его как заполнитель параметра.
    -- Возвраты входят наравне с поступлениями: они лежат в тех же фактах отрицательной суммой, и обычная
    -- метрика выручки их вычитает. Брать только положительные значило бы считать когорту по другому правилу,
    -- и при возврате у клиента-лида её выручка оказалась бы завышена молча (ревью Codex, п.2).
    SELECT DISTINCT p.payment_id, p.order_id, p.revenue FROM facts.payment p
    JOIN facts.deal d ON d.deal_id = p.deal_id
    JOIN leads l ON l.contact_id = d.contact_id
    WHERE p.money_system = ? AND p.has_basis AND p.brand = ?
      AND p.paid_at >= ? AND p.paid_at < ?
  ) q GROUP BY order_id
),
cost AS (
  SELECT order_id, SUM(cogs) AS cogs, SUM(unpriced_revenue) AS unpriced
  FROM facts.shipment_cogs WHERE money_system = ? AND order_id IS NOT NULL GROUP BY order_id
)
SELECT (SELECT COUNT(*) FROM leads),
       COALESCE(SUM(p.revenue), 0),
       COALESCE(SUM(c.cogs), 0),
       COALESCE(SUM(c.unpriced), 0),
       COUNT(*),                                              -- строк оплат: заказы плюс оплаты без заказа
       COUNT(c.order_id),                                     -- из них с известной себестоимостью
       COALESCE(SUM(CASE WHEN c.order_id IS NULL THEN p.revenue END), 0)
FROM paid p LEFT JOIN cost c ON c.order_id = p.order_id
"""


# Отбор когорты по группе каналов заявки (витрина по каналам). Маркер берётся из кабинета бренда сделки, как у
# new_first_sql_by_channel, — иначе сумма по группам не сошлась бы с числом лидов метрики.
NO_MARKER = "(нет маркера)"                # заявка без заказа в трекере: канал неизвестен
_COHORT_CHANNEL = ("\n    AND EXISTS (SELECT 1 FROM facts.order_marker om WHERE om.deal_id = d.deal_id"
                   " AND om.source_system = ? AND COALESCE(om.marker_level_1, '') = ?)")
_COHORT_NO_MARKER = ("\n    AND NOT EXISTS (SELECT 1 FROM facts.order_marker om WHERE om.deal_id = d.deal_id"
                     " AND om.source_system = ?)")


def _lead_cohort(engine, cfg, scope: str, lo: datetime, hi: datetime, channel: str | None = None):
    """Числа когорты лидов одним запросом: лиды, выручка, себестоимость, неполнота.

    Все величины берутся из ОДНОЙ выборки: иначе выручка и себестоимость посчитались бы по разным множествам
    заказов, и разность перестала бы быть валовой прибылью. channel — группа каналов заявки: значение маркера,
    EMPTY_GROUP для пустого, NO_MARKER для заявок без заказа в трекере; None — все заявки."""
    system = _money_system(cfg)
    extra, extra_params = "", ()
    if channel == NO_MARKER:
        extra, extra_params = _COHORT_NO_MARKER, (_tracking_system(cfg, scope),)
    elif channel is not None:
        extra = _COHORT_CHANNEL
        extra_params = (_tracking_system(cfg, scope), "" if channel == EMPTY_GROUP else channel)
    row = engine.fetchone(_COHORT_SQL.format(channel=extra),
                          (scope, lo, hi, *extra_params, system, scope, lo, hi, system))
    leads, revenue, cogs_sum, unpriced, orders, with_cost, unshipped_paid = row
    return dict(leads=int(leads), revenue=float(revenue), cogs=float(cogs_sum), unpriced=float(unpriced),
                orders=int(orders), with_cost=int(with_cost), unshipped_paid=float(unshipped_paid))


def _cohort_notes(c: dict) -> list[str]:
    """Чего не хватает числу когорты. Каждая величина названа суммой: доля неполноты видна, а не спрятана."""
    notes = []
    if c["unpriced"]:
        notes.append(f"позиций без закупочной цены на {_rub(c['unpriced'])} ₽ продаж — "
                     f"их себестоимость не учтена")
    # Оплата без известной себестоимости — это либо заказ, который ещё не отгружен, либо платёж вовсе без заказа
    # (розница, предоплата без оформления). Обе причины выглядят одинаково в числе и обе обязаны быть названы:
    # без этого из выручки вычлась бы себестоимость только части проданного, и прибыль оказалась бы завышена.
    missing_orders = c["orders"] - c["with_cost"]
    if missing_orders:
        notes.append(f"оплат без известной себестоимости {missing_orders} на {_rub(c['unshipped_paid'])} ₽ — "
                     f"заказ не отгружен или платёж не привязан к заказу")
    notes.append("верхняя оценка: клиент мог заплатить по заявке прошлого периода")
    return notes


def lead_conversion(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                    segment: str = "") -> Number:
    """Доля лидов периода, чей клиент заплатил в том же периоде. Переход заявка → продажа идёт через контакт."""
    if segment:
        raise RuleViolation("К2", f"у конверсии лида нет разреза «{segment}»")
    system = _money_system(cfg)
    lo, hi = window(start, end, cfg.timezone)

    def one(s):
        if as_of is not None:
            snap = from_snapshot(engine, "lead_conversion", s, start, end, as_of, "", level="deal", flow="all",
                                 unit="доля", denominator="лидов",
                                 source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (*_crm_systems(cfg), system), as_of, "CRM и денег")
        common = dict(metric="lead_conversion", level="deal", scope=s, flow="all", period_start=start,
                      period_end=end, source=f"D:{SOURCE_SYSTEM}:facts.deal × facts.payment по контакту",
                      unit="доля", denominator="лидов", as_of=state)
        gap = coverage_note(engine, system, lo, hi)
        if gap:
            return Number(status=Status.NO_DATA, value=None, missing="; ".join(x for x in (gap, note) if x), **common)
        c = _lead_cohort(engine, cfg, s, lo, hi)
        if not c["leads"]:
            return Number(status=Status.NO_DATA, value=None, denominator_value=0.0,
                          missing="; ".join(x for x in ("лидов в периоде нет — делить не на что", note) if x), **common)
        converted = engine.fetchone(
            "WITH leads AS (SELECT DISTINCT d.contact_id FROM facts.deal d "
            "  WHERE d.is_new_first AND d.deleted_at IS NULL AND d.contact_id IS NOT NULL AND d.brand = ? "
            "    AND d.created_at >= ? AND d.created_at < ? "
            "    AND NOT EXISTS (SELECT 1 FROM facts.exclusion e WHERE e.entity = 'deal' "
            "                    AND e.entity_key = CAST(d.deal_id AS VARCHAR))) "
            "SELECT COUNT(DISTINCT l.contact_id) FROM leads l "
            "WHERE EXISTS (SELECT 1 FROM facts.payment p JOIN facts.deal d2 ON d2.deal_id = p.deal_id "
            "  WHERE d2.contact_id = l.contact_id AND p.money_system = ? AND p.has_basis AND p.revenue > 0 "
            "    AND p.brand = ? AND p.paid_at >= ? AND p.paid_at < ?)",
            (s, lo, hi, system, s, lo, hi))[0]
        notes = [note] if note else []
        notes.append("верхняя оценка: клиент мог заплатить по заявке прошлого периода")
        return Number(status=Status.ESTIMATE, value=converted / c["leads"], denominator_value=float(c["leads"]),
                      missing="; ".join(notes), **common)
    return _per_scope(cfg, "lead_conversion", scope, one)


def _cohort_money(engine, cfg, scope: str, start: date, end: date, as_of: date | None,
                  metric: str, pick, unit: str = "₽") -> Number:
    """Общая часть денежных метрик когорты: выручка и валовая прибыль считаются одинаково, берётся разное поле.

    Одна выборка на обе величины — тогда `gross_profit_leads = revenue_leads − cogs_leads` верно арифметически,
    а не на глаз."""
    system = _money_system(cfg)
    lo, hi = window(start, end, cfg.timezone)

    def one(s):
        if as_of is not None:
            snap = from_snapshot(engine, metric, s, start, end, as_of, "", level="payment", flow="all", unit=unit,
                                 source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (*_crm_systems(cfg), system), as_of, "CRM и денег")
        common = dict(metric=metric, level="payment", scope=s, flow="all", period_start=start, period_end=end,
                      source=f"D:{SOURCE_SYSTEM}:facts.payment × facts.deal по контакту", unit=unit, as_of=state)
        # Выручка когорты опирается на деньги, себестоимость — на отгрузки: проверяются оба покрытия.
        gap = coverage_note(engine, system, lo, hi) or (
            coverage_note(engine, f"{system}-cogs", lo, hi) if metric != "revenue_leads" else "")
        if gap:
            return Number(status=Status.NO_DATA, value=None, missing="; ".join(x for x in (gap, note) if x), **common)
        c = _lead_cohort(engine, cfg, s, lo, hi)
        value = pick(c)
        if value is None:
            return Number(status=Status.NO_DATA, value=None,
                          missing="; ".join(x for x in ("по оплаченным заказам когорты нет отгрузок — "
                                                        "себестоимость неизвестна", note) if x), **common)
        notes = ([note] if note else []) + _cohort_notes(c)
        return Number(status=Status.ESTIMATE, value=value, missing="; ".join(notes), **common)
    return _per_scope(cfg, metric, scope, one)


def revenue_leads(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                  segment: str = "") -> Number:
    """Выручка когорты: деньги, пришедшие в периоде от клиентов, которые в этом же периоде оставили заявку."""
    if segment:
        raise RuleViolation("К2", f"у выручки когорты нет разреза «{segment}»")
    return _cohort_money(engine, cfg, scope, start, end, as_of, "revenue_leads", lambda c: c["revenue"])


def cogs_leads(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
               segment: str = "") -> Number:
    """Себестоимость проданного той же когорте."""
    if segment:
        raise RuleViolation("К2", f"у себестоимости когорты нет разреза «{segment}»")
    return _cohort_money(engine, cfg, scope, start, end, as_of, "cogs_leads",
                         lambda c: c["cogs"] if c["with_cost"] else None)


def gross_profit_leads(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                       segment: str = "") -> Number:
    """Валовая прибыль когорты: выручка минус себестоимость, обе из одной выборки заказов."""
    if segment:
        raise RuleViolation("К2", f"у валовой прибыли когорты нет разреза «{segment}»")
    return _cohort_money(engine, cfg, scope, start, end, as_of, "gross_profit_leads",
                         lambda c: (c["revenue"] - c["cogs"]) if c["with_cost"] else None)


def ampu_per_lead(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                  segment: str = "") -> Number:
    """Средняя валовая прибыль на лид: деньги когорты, пришедшей заявками в периоде.

    Это ДРУГОЕ число, чем `ampu`: там знаменатель — оплаченные сделки, здесь — заявки. За июн–авг 2026 первое
    даёт 39 697 ₽, второе 9 163 ₽; известный ответ модели юнит-экономики (снимок 03.09) — 13 631 ₽. Три ответа
    на три разных вопроса, а не одно число с ошибкой.

    Выручка и себестоимость берутся из одной выборки заказов, поэтому разность — действительно валовая прибыль
    когорты, а не разность по разным множествам."""
    if segment:
        raise RuleViolation("К2", f"у средней прибыли на лид нет разреза «{segment}»")
    system = _money_system(cfg)
    lo, hi = window(start, end, cfg.timezone)

    def one(s):
        if as_of is not None:
            snap = from_snapshot(engine, "ampu_per_lead", s, start, end, as_of, "", level="deal", flow="all",
                                 unit="₽", denominator="лидов", source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, (*_crm_systems(cfg), system), as_of, "CRM и денег")
        common = dict(metric="ampu_per_lead", level="deal", scope=s, flow="all", period_start=start,
                      period_end=end, source=f"D:{SOURCE_SYSTEM}:(facts.payment − facts.shipment_cogs) ÷ лиды",
                      unit="₽", denominator="лидов", as_of=state)
        gap = coverage_note(engine, system, lo, hi)
        if gap:
            return Number(status=Status.NO_DATA, value=None, missing="; ".join(x for x in (gap, note) if x), **common)
        c = _lead_cohort(engine, cfg, s, lo, hi)
        if not c["leads"]:
            return Number(status=Status.NO_DATA, value=None, denominator_value=0.0,
                          missing="; ".join(x for x in ("лидов в периоде нет — делить не на что", note) if x), **common)
        if not c["with_cost"]:
            return Number(status=Status.NO_DATA, value=None, denominator_value=float(c["leads"]),
                          missing="; ".join(x for x in ("по оплаченным заказам когорты нет отгрузок — "
                                                        "себестоимость неизвестна", note) if x), **common)
        profit = c["revenue"] - c["cogs"]
        notes = ([note] if note else []) + _cohort_notes(c)
        return Number(status=Status.ESTIMATE, value=profit / c["leads"], denominator_value=float(c["leads"]),
                      missing="; ".join(notes), **common)
    return _per_scope(cfg, "ampu_per_lead", scope, one)


def paid_deals_crm(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                   segment: str = "") -> Number:
    """Выигранные сделки CRM воронок продаж по дате закрытия.

    Это не «оплаченные сделки» метрики `payments`: там сделки, по которым пришли деньги, по всем воронкам и в любом
    статусе. Здесь — то, что считает финансовый контур и известный ответ: сделка доведена до выигрышного статуса в
    воронке продаж. За июн–авг 2026 первое даёт 976 и 177, второе — 679 и 49; разница в определении, а не в данных."""
    if segment:
        raise RuleViolation("К2", f"у выигранных сделок нет разреза «{segment}»")
    if not cfg.crm:
        raise RuleViolation("К2", "в конфигурации нет секции CRM — выигранные сделки считать не из чего")
    lo, hi = window(start, end, cfg.timezone)
    pipelines = [str(x) for x in cfg.crm["sales_pipelines"]]
    won = str(cfg.crm["won_status"])

    def one(s):
        if as_of is not None:
            snap = from_snapshot(engine, "paid_deals_crm", s, start, end, as_of, "", level="deal", flow="all",
                                 unit="шт", source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
            if snap is not None:
                return snap
        state, note = _checked_state(engine, _crm_systems(cfg), as_of, "CRM")
        marks = ", ".join("?" for _ in pipelines)
        n = engine.fetchone(f"SELECT COUNT(*) FROM facts.deal WHERE brand = ? AND status = ? "
                            f"AND pipeline IN ({marks}) AND closed_at >= ? AND closed_at < ? AND deleted_at IS NULL",
                            (s, won, *pipelines, lo, hi))[0]
        return Number(metric="paid_deals_crm", level="deal", scope=s, flow="all", period_start=start, period_end=end,
                      source=f"D:{SOURCE_SYSTEM}:facts.deal won", status=Status.ESTIMATE if note else Status.FACT,
                      value=float(n), unit="шт", as_of=state, missing=note)
    return _per_scope(cfg, "paid_deals_crm", scope, one)


def _attribution_share(engine, cfg, scope: str, start: date, end: date, as_of: date | None,
                       contour: str, *, known: bool) -> Number:
    """Доля сделок, у которых канал входа заполнен («источник известен») или пуст («без следа»).

    Знаменатель — те же сделки при том же наборе отбора, поэтому доли складываются в единицу. «Без следа» — это
    пустой канал входа СДЕЛКИ: определение снято с интерфейса системы сквозной аналитики («Неизвестное значение» в группировке
    «Канал входа») и проверено на фактах 16.09.2026 — 40 сделок, 6,3 % августа, 38 + 2 по кабинетам. По пустому
    маркеру визита их было бы 96 — это другое число и другой вопрос."""
    from datacore.serve.contour import contour_clause
    metric = "source_known" if known else "no_trace"
    segment = "" if contour == "full" else f"contour={contour}"
    clause, clause_params = contour_clause(cfg, contour)
    if scope not in cfg.scopes:
        raise RuleViolation("К2", f"кабинет «{scope}» не объявлен в конфигурации: {cfg.scopes}")
    common = dict(metric=metric, level="new_first_sql", scope=scope, flow="all", period_start=start,
                  period_end=end, segment=segment, source=f"D:{SOURCE_SYSTEM}:facts.deal", unit="доля",
                  denominator="new_first_sql")
    if as_of is not None:
        snap = from_snapshot(engine, metric, scope, start, end, as_of, segment, level="new_first_sql", flow="all",
                             unit="доля", denominator="new_first_sql",
                             source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
        if snap is not None:
            return snap
    state, note = _checked_state(engine, _crm_systems(cfg), as_of, "CRM")
    lo, hi = window(start, end, cfg.timezone)
    join = _own_marker_join(cfg)
    where = " AND " + clause if clause else ""
    brand_cond = "" if scope == "company" else " AND d.brand = ?"
    brand_args = () if scope == "company" else (scope,)
    test = "<>" if known else "="
    total, hit = engine.fetchone(
        f"SELECT COUNT(DISTINCT d.deal_id), "
        f"COUNT(DISTINCT CASE WHEN COALESCE(d.entry_channel_summary, '') {test} '' THEN d.deal_id END) "
        f"FROM facts.deal d {join} WHERE d.deleted_at IS NULL{brand_cond} "
        "AND d.created_at >= ? AND d.created_at < ? AND NOT EXISTS (SELECT 1 FROM facts.exclusion e "
        f"WHERE e.entity = 'deal' AND e.entity_key = CAST(d.deal_id AS VARCHAR)){where}",
        (*brand_args, lo, hi, *clause_params))
    if not total:
        return Number(status=Status.NO_DATA, value=None, as_of=state,
                      missing="; ".join(x for x in ("в окне нет сделок набора — делить не на что", note) if x),
                      **common)
    return Number(status=Status.ESTIMATE if note else Status.FACT, value=hit / total, as_of=state,
                  missing=note, denominator_value=float(total), **common)


def source_known(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
                 contour: str = "full") -> Number:
    """Доля сделок с известным каналом входа."""
    return _attribution_share(engine, cfg, scope, start, end, as_of, contour, known=True)


def no_trace(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
             contour: str = "full") -> Number:
    """Доля сделок без следа: канал входа пуст."""
    return _attribution_share(engine, cfg, scope, start, end, as_of, contour, known=False)


def conversion(engine, cfg, scope: str, start: date, end: date, as_of: date | None = None,
               segment: str = "", contour: str = "full") -> Number:
    """Конверсия визита в сделку по группе канала.

    ⚠️ Знаменатель — ПОЛНЫЕ визиты группы, без отбора сделок. Фильтр по полю сделки в витрине поставщика отсекает
    визиты (61 404 → 629) и превращает конверсию 0,96 % в 89,98 % — ядро так не делает: отбор сделок и полнота
    визитов независимы. Проверено на фактах 16.09.2026: SEO 249 / 50 659 = 0,49 %."""
    from datacore.serve.contour import contour_clause
    if not segment.startswith(CHANNEL_GROUP):
        raise RuleViolation("К2", f"у конверсии разрез только «{CHANNEL_GROUP}<группа>», получено «{segment}»")
    group = segment[len(CHANNEL_GROUP):]
    if scope not in cfg.scopes or scope == "company":
        # Конверсия считается по кабинету: визиты двух сайтов складывать нельзя (К7, доказательства нет).
        raise RuleViolation("К2", f"конверсия считается по кабинету, не по «{scope}»")
    system = _tracking_system(cfg, scope)
    clause, clause_params = contour_clause(cfg, contour)
    full_segment = segment if contour == "full" else f"{segment};{CONTOUR_SEGMENT}{contour}"
    lo, hi = window(start, end, cfg.timezone)
    common = dict(metric="conversion", level="new_first_sql", scope=scope, flow="all", period_start=start,
                  period_end=end, segment=full_segment, source=f"D:{SOURCE_SYSTEM}:facts.visit × facts.deal",
                  unit="доля", denominator="visits")
    if as_of is not None:
        snap = from_snapshot(engine, "conversion", scope, start, end, as_of, full_segment, level="new_first_sql",
                             flow="all", unit="доля", denominator="visits",
                             source=f"D:{SOURCE_SYSTEM}:facts.snapshot_number")
        if snap is not None:
            return snap
    state, note = _checked_state(engine, (*_crm_systems(cfg), system), as_of, "CRM и визитов")
    gap = coverage_note(engine, system, lo, hi)
    bots = tuple((cfg.tracking or {}).get("bot_groups", []))
    robots = (" AND COALESCE(marker_level_1, '') NOT IN (" + ", ".join("?" for _ in bots) + ")") if bots else ""
    visits_n = engine.fetchone(
        f"SELECT COUNT(*) FROM facts.visit WHERE source_system = ? AND marker_level_1 = ? "
        f"AND started_at >= ? AND started_at < ?{robots}", (system, group, lo, hi, *bots))[0]
    if gap or not visits_n:
        reason = gap or f"визитов группы «{group}» в окне нет — делить не на что"
        return Number(status=Status.NO_DATA, value=None, as_of=state,
                      missing="; ".join(x for x in (reason, note) if x), **common)
    join = _own_marker_join(cfg)
    where = " AND " + clause if clause else ""
    deals_n = engine.fetchone(
        f"SELECT COUNT(DISTINCT d.deal_id) FROM facts.deal d {join} WHERE d.deleted_at IS NULL "
        "AND d.brand = ? AND m.marker_level_1 = ? AND d.created_at >= ? AND d.created_at < ? "
        "AND NOT EXISTS (SELECT 1 FROM facts.exclusion e WHERE e.entity = 'deal' "
        f"AND e.entity_key = CAST(d.deal_id AS VARCHAR)){where}",
        (scope, group, lo, hi, *clause_params))[0]
    return Number(status=Status.ESTIMATE if note else Status.FACT, value=deals_n / visits_n, as_of=state,
                  missing=note, denominator_value=float(visits_n), **common)


REGISTRY = {"new_first_sql": new_first_sql, "visits": visits, "calls": calls,
            "new_first_sql_by_channel": new_first_sql_by_channel, "revenue": revenue, "payments": payments, "source_known": source_known, "no_trace": no_trace, "conversion": conversion,
            "paid_deals_crm": paid_deals_crm, "cogs": cogs, "gross_profit": gross_profit, "ampu": ampu,
            "lead_conversion": lead_conversion, "revenue_leads": revenue_leads, "cogs_leads": cogs_leads,
            "gross_profit_leads": gross_profit_leads, "ampu_per_lead": ampu_per_lead}
