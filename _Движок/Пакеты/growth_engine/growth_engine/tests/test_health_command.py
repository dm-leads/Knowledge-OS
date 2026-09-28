"""Команда гейта инстанса (задачи 6.2–6.3, правки по ревью) на моделях источников и хранилища: порядок разделов, четыре
прогона ворот этапа — чисто → 0; истёкший токен → 🟧 1; смешанная метрика → 🟥 1; просроченный отложенный замер → 🟧 1;
известные ответы и пересмотры; отсутствующие разделы, пустой ответ источника, чистка вывода; гейт ничего не пишет."""
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

import yaml

from growth_engine.core.number import Number, Status
from growth_engine.core.registry import HStatus, transition
from growth_engine.core.source_map import load_source_map
from growth_engine.core.storage import RegistryStore
from growth_engine.health import run
from growth_engine.sources.base import ProbeResult
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.fake_lark import FakeLarkBridge
from growth_engine.tests.helpers import AUG, CFG, JUL, LAUNCH, candidate, gated, n
from growth_engine.tests.test_health_core import P1, P2, TREE, snapshot

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 15)
# Эффект гипотезы в единицах цели строит модель цикла — система «модель», как требует правило номеров чисел.
MODEL_EFFECT = n("sales_entry", 12, status=Status.ESTIMATE, source="C:модель:эффект гипотезы")
# Суммы кабинетов по закрытым месяцам в снимке 03.09 и ответы источника сегодня.
MONTHS = [n("sales_entry", 950, scope="p1+p2", period=JUL), n("sales_entry", 1994, scope="p1+p2", period=AUG)]
VALUES = {("sales_entry", "p1", AUG): 1860, ("sales_entry", "p2", AUG): 134,
          ("sales_entry", "p1", JUL): 900, ("sales_entry", "p2", JUL): 50}
MARKS = ("✅", "🟥", "🟧", "⬜", "⏭")
KNOWN = {"system": "analytics-x", "metric": "sales_entry", "scope": "p1", "flow": "all",
         "period": ["2026-08-01", "2026-09-01"], "abs_units": 0, "rel": 0.01, "changes_since": "2026-09-03",
         "checked": "снимок 03.09.2026 — тест"}


class FakeSource:
    """Модель источника: проба, число окна и номера записей, изменённых после даты."""

    def __init__(self, ok, values, empty=False, detail=None):
        self.ok, self.values, self.empty, self.detail = ok, values, empty, detail

    def probe(self):
        detail = self.detail or ("ответ получен" if self.ok else "доступ отклонён")
        return [ProbeResult("аналитика-x", self.ok, 200 if self.ok else 401, detail)]

    def fetch(self, query):
        if self.empty:
            return []
        value = self.values.get((query.metric, query.scope, (query.period_start, query.period_end)))
        return [Number(metric=query.metric, level=CFG.rule(query.metric).level, scope=query.scope, flow=query.flow,
                       period_start=query.period_start, period_end=query.period_end, source="C:analytics-x:data",
                       status=Status.FACT if value is not None else Status.NO_DATA, value=value, as_of=query.as_of,
                       missing="" if value is not None else "окна нет в ответе")]

    def changed_since(self, query, since):
        return [101, 102]


def instance(tmp_path, storage=None, **health):
    raw = yaml.safe_load((FIXTURES / "config_gate.yaml").read_text(encoding="utf-8"))
    raw["health"] = {**raw["health"], "source_map": str(FIXTURES / "source_map_minimal.yaml"), **health}
    if storage:
        raw["storage"] = storage
    config = tmp_path / "конфигурация.yaml"
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    secrets = tmp_path / "секреты.env"
    secrets.write_text("API_KEY_X=x\nWEB_TOKEN_Y=y\n", encoding="utf-8")
    return config, secrets


def filled_store(folder, *hypotheses, numbers=None):
    store = RegistryStore(MarkdownBackend(folder), today=lambda: TODAY)
    store.ensure_sheets()
    store.create_numbers((snapshot(P1, P2) if numbers is None else numbers) + MONTHS)
    store.create(TREE)
    for hypothesis in hypotheses:
        store.create(hypothesis)
    store.sync_sources(load_source_map(FIXTURES / "source_map_minimal.yaml"))
    return folder


def gate(tmp_path, folder=None, storage=None, today=TODAY, ok=True, values=None, empty=False, detail=None, bridge=None,
         **health):
    config, secrets = instance(tmp_path, storage, **health)
    args = Namespace(config=str(config), secrets=str(secrets), out=None if folder is None else str(folder),
                     confirm_paid=False)
    source = FakeSource(ok, {**VALUES, **(values or {})}, empty, detail)
    lines = []
    code = run(args, out=lines.append, today=lambda: today,
               factories={"analytics-x": lambda section, cfg, source_secrets, paid: source},
               bridge_factory=lambda: bridge or FakeLarkBridge())
    checks = [(line[0], line.split("] ", 1)[1].split(":", 1)[0]) for line in lines if line.startswith(MARKS)]
    return code, lines, checks


def violations(checks):
    return [check for check in checks if check[0] in ("🟥", "🟧")]


def clean_store(tmp_path):
    return filled_store(tmp_path / "хранилище", candidate("H-001", effect_goal_units=MODEL_EFFECT))


# --- четыре прогона ворот ---

def test_clean_instance_gives_code_0_and_writes_nothing(tmp_path):
    folder = clean_store(tmp_path)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    code, lines, checks = gate(tmp_path, folder)
    assert code == 0 and lines[-1].startswith("ИТОГ: гейт зелёный")
    assert checks == [("✅", "конфигурация"), ("✅", "карта источников"), ("✅", "источники"), ("⏭", "источники"),
                      ("✅", "известные ответы"), ("✅", "хранилище"), ("✅", "модель"), ("✅", "модель"),
                      ("✅", "модель"), ("✅", "модель"), ("✅", "пересмотры"), ("✅", "пересмотры"), ("✅", "реестр"),
                      ("✅", "артефакты"), ("✅", "карта источников")]
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


def test_expired_store_token_is_coverage_and_later_sections_are_not_checked(tmp_path):
    bridge = FakeLarkBridge()
    bridge.fail_with = "UserAccessToken is invalid or expired (code=99991668)"
    code, lines, checks = gate(tmp_path, storage={"adapter": "lark_sheets", "book": "книга-проба"}, bridge=bridge)
    assert code == 1 and violations(checks) == [("🟧", "хранилище")]
    assert checks[-4:] == [("⬜", "модель"), ("⬜", "пересмотры"), ("⬜", "реестр"), ("⬜", "артефакты")]
    assert "повторная авторизация" in next(line for line in lines if line.startswith("🟧"))


def test_mixed_metric_in_the_snapshot_is_structure(tmp_path):
    numbers = [replace(x, unit="USD") if (x.metric, x.scope) == ("gross_profit", "p1") else x for x in snapshot(P1)]
    code, lines, checks = gate(tmp_path, filled_store(tmp_path / "хранилище", numbers=numbers))
    assert code == 1 and violations(checks) == [("🟥", "модель")]
    assert any(line.startswith("🟥 [страж 4] модель: кабинет p1") for line in lines)


def test_overdue_deferred_measurement_is_coverage(tmp_path):
    launched = transition(gated("H-001", effect_goal_units=MODEL_EFFECT), HStatus.IN_TEST, **LAUNCH)
    deferred = transition(launched, HStatus.DEFERRED, deferred_until=date(2026, 11, 15))
    folder = filled_store(tmp_path / "хранилище", deferred)
    code, lines, checks = gate(tmp_path, folder, today=date(2026, 11, 16), model_snapshot_max_days=90,
                               revisions={"off": "тест: закрытые месяцы ноября в снимке не хранятся"})
    assert code == 1 and violations(checks) == [("🟧", "реестр")]
    assert any("назначен на 15.11.2026" in line for line in lines) and ("⬜", "пересмотры") in checks


# --- известные ответы и пересмотры ---

def test_known_answer_out_of_tolerance_is_structure_with_changed_records(tmp_path):
    code, lines, checks = gate(tmp_path, clean_store(tmp_path), known_answers=[{**KNOWN, "expected": 1700}])
    assert code == 1 and violations(checks) == [("🟥", "известные ответы")]
    line = next(line for line in lines if line.startswith("🟥"))
    assert line.startswith("🟥 [страж 2] известные ответы: sales_entry · кабинет p1") and "номера: 101, 102" in line


def test_known_answer_written_without_required_keys_is_structure(tmp_path):
    code, lines, checks = gate(tmp_path, clean_store(tmp_path), known_answers=[{"system": "analytics-x"}])
    assert code == 1 and violations(checks) == [("🟥", "известные ответы")] and any("не задано" in x for x in lines)


def test_revision_of_a_closed_month_beyond_tolerance_is_coverage(tmp_path):
    code, lines, checks = gate(tmp_path, clean_store(tmp_path), values={("sales_entry", "p1", JUL): 950})
    assert code == 1 and violations(checks) == [("🟧", "пересмотры")]
    assert any("съём 03.09.2026 — 950, съём 15.09.2026 — 1 000" in line and "вне допуска" in line for line in lines)


# --- правки по ревью Codex этапа 6: ложный зелёный и падения ---

def test_missing_known_answers_section_is_coverage_not_green(tmp_path):
    code, _, checks = gate(tmp_path, clean_store(tmp_path), known_answers=None)
    assert code == 1 and violations(checks) == [("🟧", "известные ответы")]


def test_missing_revisions_section_is_coverage_not_green(tmp_path):
    code, _, checks = gate(tmp_path, clean_store(tmp_path), revisions=None)
    assert code == 1 and violations(checks) == [("🟧", "пересмотры")]


def test_empty_source_answer_is_coverage_not_a_crash(tmp_path):
    code, lines, checks = gate(tmp_path, clean_store(tmp_path), empty=True)
    assert code == 1 and violations(checks) == [("🟧", "известные ответы"), ("🟧", "пересмотры")]
    assert lines[-1].startswith("ИТОГ: гейт НЕ пройден") and any("пустой ответ" in line for line in lines)


def test_output_hides_url_query_emails_and_tokens(tmp_path):
    detail = "ответ https://api.example.com/v1?token=abc123 для user@example.com, ключ A1b2C3d4E5f6G7h8I9j0K1l2"
    code, lines, _ = gate(tmp_path, clean_store(tmp_path), detail=detail)
    text = "\n".join(lines)
    assert code == 0 and "token=abc123" not in text and "user@example.com" not in text
    assert "A1b2C3d4E5f6G7h8I9j0K1l2" not in text and "ключ …" in text


def test_markdown_store_folder_that_does_not_exist_is_not_created(tmp_path):
    folder = tmp_path / "нет такой папки"
    code, _, checks = gate(tmp_path, folder)
    assert code == 1 and not folder.exists() and ("🟧", "артефакты") in checks


# --- отказы ---

def test_source_probe_without_access_is_coverage(tmp_path):
    code, _, checks = gate(tmp_path, clean_store(tmp_path), ok=False)
    assert code == 1 and violations(checks) == [("🟧", "источники")]


def test_store_not_declared_is_structure_and_later_sections_are_not_checked(tmp_path):
    code, _, checks = gate(tmp_path)
    assert code == 1 and violations(checks) == [("🟥", "хранилище")]
    assert checks[-4:] == [("⬜", "модель"), ("⬜", "пересмотры"), ("⬜", "реестр"), ("⬜", "артефакты")]


def test_unreadable_config_stops_with_structure(tmp_path):
    args = Namespace(config=str(tmp_path / "нет.yaml"), secrets=str(tmp_path / "секреты.env"), out=None,
                     confirm_paid=False)
    lines = []
    assert run(args, out=lines.append, today=lambda: TODAY) == 1
    assert lines[0].startswith("🟥 [страж 13] конфигурация") and lines[-1].startswith("ИТОГ: гейт НЕ пройден")
