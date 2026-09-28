"""Подкоманда `bottleneck` (задача 7б.9): узкое место на единице действия — последняя ветка команды реестра.

Читающая: считает по числам хранилища и ничего не пишет. Ядро написано задачей 7б.0 (`core/bottleneck.py`): переходы
между ступенями по объявленному порядку, конверсии через `ratio()`, порядок кандидатов «маржа → чек → конверсия →
поток», и главное — узкое место, названное метрикой, не принимается (страж 6, ворота шага 3).

Числа ступеней отбираются из хранилища фильтрами, как в `shift-share`: по метрике, окну, кабинету и потоку.
"""
import json
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

from growth_engine.core.number import Status
from growth_engine.core.storage import RegistryStore, number_id
from growth_engine.registry import SUBCOMMANDS, run
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.helpers import AUG, CFG, n

FIXTURES = Path(__file__).parent / "fixtures"
# Своя конфигурация с разделом economy: воронка отбирает числа системы-источника, отделяя их от чисел модели.
# В общую фикстуру economy добавить нельзя — команда маршрута начинает выводить из ролей то, что должна спрашивать.
CONFIG = str(FIXTURES / "config_funnel.yaml")
TODAY = date(2026, 9, 15)

# Воронка окна: визиты 190 468 → заявки 7 188 → квалифицированные 2 418 → вход продаж 1 860 → продажи 410.
# Самый узкий переход — «визит → заявка» (3,77%), как в известном ответе снимка воронки инстанса (задача 7б.0).
FUNNEL = {"visits": 190468.0, "leads": 7188.0, "qualified": 2418.0, "sales_entry": 1860.0, "sales": 410.0}
MARGIN = replace(n("gross_profit", 0.366, period=AUG), unit="доля", denominator="pnl_revenue")
CHECK = replace(n("avg_check", 124010.0, period=AUG), unit="₽ на sales", denominator="sales")


def funnel_numbers(scope="p1", flow="all"):
    return [n(metric, value, scope=scope, flow=flow, period=AUG) for metric, value in FUNNEL.items()]


def store(folder) -> RegistryStore:
    return RegistryStore(MarkdownBackend(Path(folder)), allowed_domains=(), today=lambda: TODAY)


def with_numbers(folder):
    s = store(folder)
    s.create_numbers(funnel_numbers() + [MARGIN, CHECK])
    return s


def call(folder, **changes):
    args = Namespace(cmd="bottleneck", config=CONFIG, out=str(folder), lark_book=None, dry_run=False, cycle="Ц-1",
                     id=None, status=None, json=None, secrets=None, source=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: TODAY), lines


def ask_json(**changes) -> str:
    """Запрос узкого места: окно, кабинет, поток, ступени воронки и — необязательно — деньги парой чисел.

    Деньги подаются номерами записанных чисел «сейчас» и «раньше»: кандидаты «маржа» и «чек» сравниваются между
    окнами, а число значением задать нельзя (стражи 1 и 4).
    """
    fields = {"window": "2026-08", "scope": "p1", "flow": "all", "stages": list(FUNNEL)}
    fields.update(changes)
    return json.dumps({k: v for k, v in fields.items() if v is not None}, ensure_ascii=False)


# --- ступени и запас ---

def test_bottleneck_is_a_reading_subcommand():
    assert SUBCOMMANDS["bottleneck"].writes is False


def test_stage_conversions_are_printed_in_funnel_order(tmp_path):
    """Переходы идут по объявленному порядку ступеней, а не по порядку чисел в хранилище."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json())
    assert code == 0, lines[-1]
    # Считать по символу «→» нельзя: его же содержит строка вывода про узкое место. Переходы — строки с отступом.
    printed = [line.strip() for line in lines if line.startswith("   ") and "→" in line]
    assert len(printed) == 4, printed
    assert printed[0].startswith("visits → leads") and printed[-1].startswith("sales_entry → sales")


def test_narrowest_stage_is_named(tmp_path):
    """Самый узкий переход окна — «визит → заявка»: это и есть место, где копится запас."""
    with_numbers(tmp_path)
    _, lines = call(tmp_path, json=ask_json())
    assert any("visits → leads" in line and "узкое место" in line for line in lines)


def test_nothing_is_written(tmp_path):
    with_numbers(tmp_path)
    before = {p.name: p.read_text(encoding="utf-8") for p in Path(tmp_path).iterdir()}
    call(tmp_path, json=ask_json())
    after = {p.name: p.read_text(encoding="utf-8") for p in Path(tmp_path).iterdir()}
    assert before == after


def test_stages_are_taken_from_the_money_system_only(tmp_path):
    """Дефект приёмочного прогона 7б: числа модели ломали воронку шага 3.

    За одно окно метрика цели лежит в хранилище и от системы-источника (факт), и от модели (цель окна, потолки
    веток, эффект гипотезы). Отбор без системы брал их все, строил ступень «метрика → та же метрика» и сам же
    отвергал её стражем 4 — шаг 3 становился непроходим при любом написании ключей.
    """
    s = with_numbers(tmp_path)
    goal = replace(n("sales_entry", 850, period=AUG), source="C:модель:цель окна", status=Status.ESTIMATE)
    effect = replace(n("sales_entry", 12, period=AUG), source="C:модель:эффект гипотезы", status=Status.ESTIMATE)
    s.create_numbers([goal, effect])
    code, lines = call(tmp_path, json=ask_json())
    assert code == 0, lines[-1]
    printed = [line.strip() for line in lines if line.startswith("   ") and "→" in line]
    assert len(printed) == 4, printed
    assert not any("sales_entry → sales_entry" in line for line in printed)


def test_stages_are_taken_from_one_take(tmp_path):
    """Дефект первого живого цикла 17.09.2026: в настоящем хранилище чисел больше одного съёма.

    Одна и та же метрика лежит и от прежнего съёма, и от нового. Отбор без даты съёма брал оба, и конверсия ступени
    падала стражем 4 («не совпадает дата съёма»). В папке прогона этого не видно — там всегда один съём.
    Воронка берёт последний съём, как это делает команда снимка.
    """
    s = with_numbers(tmp_path)
    earlier = [replace(x, as_of=date(2026, 8, 20), value=x.value * 0.9) for x in funnel_numbers()]
    s.create_numbers(earlier)
    code, lines = call(tmp_path, json=ask_json())
    assert code == 0, lines[-1]
    printed = [line for line in lines if line.startswith("   ") and "→" in line]
    assert len(printed) == 4, printed


def test_fuller_take_wins_over_a_fresher_one(tmp_path):
    """Полная воронка важнее свежести: съём с двумя ступенями не отменяет съём со всеми пятью.

    Живая книга 17.09.2026: в свежем съёме лежали только `new_first_sql` и `sales`, и воронка схлопнулась до одного
    перехода — диагноз вышел обрубленным. Берётся съём, покрывающий больше ступеней.
    """
    s = with_numbers(tmp_path)
    partial = [replace(x, as_of=date(2026, 9, 30), value=x.value * 1.1)
               for x in funnel_numbers() if x.metric in ("sales_entry", "sales")]
    s.create_numbers(partial)
    code, lines = call(tmp_path, json=ask_json())
    assert code == 0, lines[-1]
    printed = [line for line in lines if line.startswith("   ") and "→" in line]
    assert len(printed) == 4, printed
    assert any("ступеней 5 из 5" in line for line in lines), lines


def test_stage_outside_config_is_code_1(tmp_path):
    """Метрика, не объявленная в конфигурации, в воронку не берётся (страж 3)."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json(stages=list(FUNNEL) + ["sql_repeat"]))
    assert code == 1 and "sql_repeat" in lines[-1]


def test_missing_stage_numbers_is_code_1(tmp_path):
    """Ступень, по которой чисел нет, называется — вместо молчаливого пропуска."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json(window="2026-07"))
    assert code == 1 and "2026-07" in lines[-1]


def test_single_stage_is_code_1(tmp_path):
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json(stages=["visits"]))
    assert code == 1 and "две" in lines[-1]


# --- ворота шага 3: единица действия ---

def test_bottleneck_without_unit_of_action_is_not_accepted(tmp_path):
    """Ворота шага 3: узкое место, названное метрикой, не принимается — вывод говорит, чего не хватает."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json())
    assert code == 0
    assert any("единица действия не названа" in line for line in lines)


def test_bottleneck_with_unit_of_action_is_accepted(tmp_path):
    """С координатами нижней ступени класса узкое место принимается и печатается одной строкой."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json(
        unit_of_action={"source_class": "A", "coordinates": {"url": "/catalog/item/", "element": "форма, шаг 2"}}))
    assert code == 0, lines[-1]
    accepted = [line for line in lines if "единица действия:" in line]
    assert accepted and "url=/catalog/item/" in accepted[-1]


def test_unit_of_action_above_the_bottom_rung_is_code_1(tmp_path):
    """Координаты выше нижней ступени класса — страж 6: это ещё метрика, а не единица действия."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json(
        unit_of_action={"source_class": "A", "coordinates": {"url": "/catalog/item/"}}))
    assert code == 1 and "[страж 6]" in lines[-1]


# --- кандидаты: деньги раньше конверсии ---

def test_money_candidates_are_checked_before_conversion(tmp_path):
    """Канон, шаг 3: порядок кандидатов «маржа → чек → конверсия → поток» — просевшая маржа важнее конверсии."""
    s = with_numbers(tmp_path)
    fell = replace(MARGIN, value=0.31, source="C:analytics-x:маржа текущего окна")
    s.create_numbers([fell])
    code, lines = call(tmp_path, json=ask_json(margin_id=number_id(fell), margin_before_id=number_id(MARGIN)))
    assert code == 0, lines[-1]
    assert any("маржа" in line and "узкое место" in line for line in lines)


def test_money_candidate_by_id_not_by_value(tmp_path):
    """Деньги — номерами записанных чисел: значением кандидата задать нельзя (стражи 1 и 4)."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=ask_json(margin=0.31, margin_before=0.366))
    assert code == 1 and "margin_id" in lines[-1]


def test_non_additive_stage_is_named_not_skipped(tmp_path):
    """Ступень, не складывающаяся по окнам, даёт «нет данных» с причиной — а не молча выпадает из воронки."""
    with_numbers(tmp_path)
    _, lines = call(tmp_path, json=ask_json())
    stage = next(line for line in lines if "sales_entry → sales" in line)
    assert "нет данных" in stage


def test_denominator_of_unsent_candidate_comes_from_config_roles(tmp_path):
    """Знаменатель непереданного кандидата — метрика из ролей денежной модели конфигурации, а не угаданное имя.

    Красный случай собственного дефекта: код спрашивал `hasattr(cfg, "money_metrics")`, поля с таким именем нет,
    ветка была мертва и подставляла «revenue» вместо метрики инстанса. Здесь конфигурация со своими ролями.
    """
    config = tmp_path / "конфигурация.yaml"
    config.write_text((Path(CONFIG).read_text(encoding="utf-8")
                       + "\neconomy:\n  money_system: analytics-x\n"
                         "  money_metrics: {sales: sales, revenue: pnl_revenue}\n"), encoding="utf-8")
    with_numbers(tmp_path)
    _, lines = call(tmp_path, config=str(config), json=ask_json())
    candidates = [line for line in lines if "не подан" in line]
    assert candidates, lines
    assert any("pnl_revenue" in line or "маржа" in line for line in candidates)


def test_no_data_candidate_is_named_not_skipped(tmp_path):
    """`нет данных` у кандидата — не «кандидат в порядке»: он остаётся непроверенным и назван в выводе."""
    with_numbers(tmp_path)
    _, lines = call(tmp_path, json=ask_json())
    assert any("не проверен" in line or "нет данных" in line for line in lines)
