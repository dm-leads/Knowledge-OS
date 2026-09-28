"""Подкоманда `tree` (задача 7б.6): дерево цели, разрыв и связь гипотезы с веткой.

Цель и потолки веток — числа, поэтому задаются номерами записанных чисел, как карточка гипотезы. У чисел модели часть
«запрос» источника называет расчёт: иначе цель окна и потолок ветки — одна метрика, период и съём — получили бы один
номер и запись остановил бы страж 13 (урок фикстуры 7б.5).

Правила дерева проверены тестами ядра (`test_artifacts.py`): единицы, окно, съём, поток и система потолка; разрыв не
сильнее «оценки», потому что потолки веток могут пересекаться. Здесь — что команда зовёт ядро, печатает разрыв строкой
`render()` и отчитывается числом строк.
"""
import json
from argparse import Namespace
from datetime import date
from pathlib import Path

from growth_engine.core.number import Status
from growth_engine.core.storage import RegistryStore, number_id
from growth_engine.registry import SUBCOMMANDS, run
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.helpers import CARD, EFFECT, EFFECT_RUB, FACT, FORMULA, UNIT, n

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")
TODAY = date(2026, 9, 15)

# Известный ответ спецификации: цель окна 1 315, потолки веток 400 и 300, разрыв 615 (проверено прогоном).
GOAL = n("sales_entry", 1315, status=Status.ESTIMATE, source="C:модель:цель окна")
CEILING_WEB = n("sales_entry", 400, flow="web", status=Status.ESTIMATE, source="C:модель:потолок ветки")
CEILING_OFFLINE = n("sales_entry", 300, flow="no_visit", status=Status.ESTIMATE, source="C:модель:потолок ветки")
FOREIGN = n("sales_entry", 200, status=Status.ESTIMATE, source="C:analytics-x:потолок из другой системы")


def store(folder) -> RegistryStore:
    return RegistryStore(MarkdownBackend(Path(folder)), allowed_domains=(), today=lambda: TODAY)


def call(folder, cmd, **changes):
    args = Namespace(cmd=cmd, config=CONFIG, out=str(folder), lark_book=None, dry_run=False, cycle="Ц-1",
                     id=None, status=None, json=None, secrets=None, source=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: TODAY), lines


def tree_json(**changes) -> str:
    fields = {"goal_id": number_id(GOAL),
              "branches": [{"id": "B1", "name": "веб-поток", "ceiling_id": number_id(CEILING_WEB)},
                           {"id": "B2", "name": "без визита", "ceiling_id": number_id(CEILING_OFFLINE)}]}
    fields.update(changes)
    return json.dumps(fields, ensure_ascii=False)


def with_numbers(folder):
    s = store(folder)
    s.create_numbers([GOAL, CEILING_WEB, CEILING_OFFLINE, FOREIGN, FACT, EFFECT, EFFECT_RUB])
    return s


def card_json() -> str:
    card = {"formulation": FORMULA, "unit_of_action": {"source_class": UNIT.source_class,
                                                       "coordinates": dict(UNIT.coordinates)},
            "fact_basis_id": number_id(FACT), "effect_goal_units_id": number_id(EFFECT),
            "effect_rub_id": number_id(EFFECT_RUB)}
    card.update({name: CARD[name] for name in ("cycle_id", "business_task", "tree_branch", "model_lever",
                                               "mechanic", "main_metric", "owner")})
    return json.dumps(card, ensure_ascii=False)


# --- запись дерева ---

def test_tree_is_a_writing_subcommand():
    assert SUBCOMMANDS["tree"].writes is True


def test_tree_is_written_and_read_back(tmp_path):
    with_numbers(tmp_path)
    code, lines = call(tmp_path, "tree", json=tree_json())
    assert code == 0, lines[-1]
    tree, = store(tmp_path).read("trees")
    assert [b.id for b in tree.branches] == ["B1", "B2"] and tree.goal == GOAL


def test_gap_is_printed_as_a_full_number_line(tmp_path):
    """Разрыв 1 315 − 400 − 300 = 615, статус «оценка» и оговорка про пересечение потолков — строкой render()."""
    with_numbers(tmp_path)
    _, lines = call(tmp_path, "tree", json=tree_json())
    gap = next(line for line in lines if "разрыв" in line)
    assert "615" in gap and "оценка" in gap and "пересекаться" in gap


def test_tree_reports_rows_written(tmp_path):
    with_numbers(tmp_path)
    _, lines = call(tmp_path, "tree", json=tree_json())
    report = [line for line in lines if "записано" in line and "прочитано" in line]
    assert len(report) == 2, report        # числа и само дерево — по строке на лист


def test_tree_without_branches_is_code_1(tmp_path):
    """Дерево без веток не хранится (страж 13): цель без разложения — не дерево."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, "tree", json=tree_json(branches=[]))
    assert code == 1 and "[страж 13]" in lines[-1]


def test_ceiling_from_another_system_is_code_1(tmp_path):
    """Потолок ветки берётся из системы цели или из модели — иначе страж 3."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, "tree", json=tree_json(
        branches=[{"id": "B1", "name": "веб-поток", "ceiling_id": number_id(FOREIGN)}]))
    assert code == 1 and "[страж 3]" in lines[-1]


def test_unknown_number_id_is_guard_13(tmp_path):
    with_numbers(tmp_path)
    code, lines = call(tmp_path, "tree", json=tree_json(goal_id="0" * 16))
    assert code == 1 and "[страж 13]" in lines[-1]


# --- показ ---

def test_listing_shows_branches_with_ceilings(tmp_path):
    """Ветки без гипотез и без причины — дыра в покрытии: показ печатает дерево и отвечает кодом 1."""
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    code, lines = call(tmp_path, "tree")
    assert code == 1
    text = "\n".join(lines)
    assert "B1" in text and "веб-поток" in text and "400" in text and "615" in text
    assert "без гипотез и причины 2" in lines[-1]


# --- покрытие: у каждой ветки гипотезы или причина, каждая гипотеза в дереве ---

def link(folder, hid, branch):
    return call(folder, "tree", id=hid,
                json=json.dumps({"goal_id": number_id(GOAL), "branch_id": branch}, ensure_ascii=False))


def note(folder, branch, reason):
    return call(folder, "tree", json=json.dumps({"goal_id": number_id(GOAL), "branch_id": branch,
                                                 "no_hypotheses": reason}, ensure_ascii=False))


def test_full_coverage_is_code_0(tmp_path):
    """B1 — гипотеза, B2 — записанная причина: расхождений нет."""
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    link(tmp_path, "H-001", "B1")
    code, lines = note(tmp_path, "B2", "не рычаг: сделки без визита заводит отдел продаж")
    assert code == 0, lines[-1]
    code, lines = call(tmp_path, "tree")
    assert code == 0, lines
    text = "\n".join(lines)
    assert "H-001 [candidate]" in text and "гипотез нет: не рычаг" in text
    assert "расхождений покрытия 0" in lines[-1]


def test_reason_can_be_given_with_the_branch(tmp_path):
    with_numbers(tmp_path)
    branches = [{"id": "B1", "name": "веб-поток", "ceiling_id": number_id(CEILING_WEB), "no_hypotheses": "ждёт данных"},
                {"id": "B2", "name": "без визита", "ceiling_id": number_id(CEILING_OFFLINE), "no_hypotheses": "не рычаг"}]
    call(tmp_path, "tree", json=tree_json(branches=branches))
    code, lines = call(tmp_path, "tree")
    assert code == 0 and "с причиной 2" in lines[-1]


def test_hypothesis_outside_the_tree_is_a_hole(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    note(tmp_path, "B1", "ждёт данных")
    note(tmp_path, "B2", "не рычаг")
    code, lines = call(tmp_path, "tree")
    assert code == 1 and any("вне дерева: H-001" in line for line in lines)


def test_archived_hypothesis_does_not_cover_a_branch(tmp_path):
    """Ветка, где осталась только гипотеза в архиве, требует причины, как пустая; архив вне дерева — не дыра."""
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    call(tmp_path, "hypothesis", id="H-001", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    call(tmp_path, "hypothesis", id="H-002", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    link(tmp_path, "H-001", "B1")
    call(tmp_path, "hypothesis", id="H-001", status="архив")
    call(tmp_path, "hypothesis", id="H-002", status="архив")
    note(tmp_path, "B2", "не рычаг")
    code, lines = call(tmp_path, "tree")
    assert code == 1 and any("гипотез в работе нет, и причина не записана" in line for line in lines)
    assert any("вне дерева, в архиве: H-002" in line for line in lines)
    note(tmp_path, "B1", "единственная гипотеза отклонена")
    assert call(tmp_path, "tree")[0] == 0


def test_relinking_moves_the_hypothesis_out_of_its_old_branch(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    link(tmp_path, "H-001", "B1")
    link(tmp_path, "H-001", "B2")
    tree, = store(tmp_path).read("trees")
    assert tree.branches[0].hypothesis_ids == () and tree.branches[1].hypothesis_ids == ("H-001",)


def test_empty_reason_erases_it(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    note(tmp_path, "B1", "ждёт данных")
    code, lines = note(tmp_path, "B1", "")
    assert code == 0 and "стёрта" in lines[-1]
    tree, = store(tmp_path).read("trees")
    assert tree.branches[0].no_hypotheses_reason == ""


def test_reason_for_absent_branch_is_guard_13(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    code, lines = note(tmp_path, "B9", "ждёт данных")
    assert code == 1 and "[страж 13]" in lines[-1]


def test_listing_empty_says_so(tmp_path):
    code, lines = call(tmp_path, "tree")
    assert code == 0 and "не найдено" in lines[-1]


# --- связь гипотезы с веткой ---

def test_hypothesis_is_linked_to_a_branch(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    code, lines = call(tmp_path, "tree", id="H-001",
                       json=json.dumps({"goal_id": number_id(GOAL), "branch_id": "B2"}, ensure_ascii=False))
    assert code == 0, lines[-1]
    tree, = store(tmp_path).read("trees")
    assert tree.branches[1].hypothesis_ids == ("H-001",)
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert stored.tree_branch == "B2"


def test_linking_twice_writes_nothing_new(tmp_path):
    """Повтор связи — 0 новых строк: операция идемпотентна по номеру гипотезы."""
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    link = json.dumps({"goal_id": number_id(GOAL), "branch_id": "B2"}, ensure_ascii=False)
    call(tmp_path, "tree", id="H-001", json=link)
    _, lines = call(tmp_path, "tree", id="H-001", json=link)
    assert all("записано 0" in line for line in lines if "записано" in line)


def test_linking_to_absent_branch_is_code_1(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    code, lines = call(tmp_path, "tree", id="H-001",
                       json=json.dumps({"goal_id": number_id(GOAL), "branch_id": "B9"}, ensure_ascii=False))
    assert code == 1 and "[страж 13]" in lines[-1]


def test_linking_absent_hypothesis_is_code_1(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, "tree", json=tree_json())
    code, lines = call(tmp_path, "tree", id="H-404",
                       json=json.dumps({"goal_id": number_id(GOAL), "branch_id": "B1"}, ensure_ascii=False))
    assert code == 1 and "H-404" in lines[-1]
