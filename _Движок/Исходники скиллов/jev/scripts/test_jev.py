# -*- coding: utf-8 -*-
"""Проверки клиента JEV без сети и без денег: `python -m pytest test_jev.py -q`.

Сеть подменяется функцией `send`; настоящий `urlopen` в тестах запрещён — обращение к нему роняет тест.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import jev

KEY = "sk-тестовый-ключ-12345"
QUESTIONS = {
    "_зачем": "комментарий, в запрос не уходит",
    "_пороги": {"жалоба": {"действовать": 0.9, "проверить": 0.7}},
    "жалоба": {"type": "noul", "instructions": "Автор недоволен?", "criteria": {"true": "жалуется", "false": "не жалуется"}},
    "тема": {"type": "choice", "instructions": "О чём отзыв?",
             "criteria": {"монтаж": "работа мастера", "прибор": "сам прибор", "другое": "ничего из перечисленного"}},
    "сила": {"type": "score", "instructions": "Насколько автор раздражён?", "criteria": ["спокоен", "недоволен", "в ярости"]},
}
ANSWERS = {
    "жалоба": {"type": "noul", "noul": 0.95},
    "тема": {"type": "choice", "choice": "монтаж", "probabilities": {"монтаж": 0.8, "прибор": 0.15, "другое": 0.05}, "confidence": 0.7},
    "сила": {"type": "score", "score": 1.1, "legend": {"0": "спокоен", "1": "недоволен", "2": "в ярости"},
             "probabilities": {"0": 0.05, "1": 0.8, "2": 0.15}, "confidence": 0.7},
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("тест обратился к сети")
    monkeypatch.setattr(jev, "open_url", forbidden)
    monkeypatch.setattr(jev.urllib.request, "urlopen", forbidden)
    for name in (jev.KEY_VAR, jev.BASE_VAR, "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def files(tmp_path):
    questions = tmp_path / "вопросы.json"
    questions.write_text(json.dumps(QUESTIONS, ensure_ascii=False), encoding="utf-8")
    records = tmp_path / "записи.csv"
    records.write_text('id;текст\n1;"Мастер опоздал,\nно сделал хорошо"\n2;Прибор шумит\n3;\n4;Всё отлично\n', encoding="utf-8")
    return questions, records, tmp_path / "out"


class Fake:
    """Подмена сети: считает вызовы и отвечает заданным образом."""
    def __init__(self, status=200, tokens=400, key_info=None, script=None):
        self.status, self.tokens, self.key_info, self.script, self.calls = status, tokens, key_info, list(script or []), []

    def __call__(self, method, url, key, payload=None, timeout=60):
        self.calls.append((method, url, payload))
        if method == "GET":
            return 200, {"data": self.key_info}, ""
        status = self.script.pop(0) if self.script else self.status
        if status != 200:
            return status, {"detail": "ошибка"}, "ошибка"
        return 200, {"model": (payload or {}).get("model", "jev-1.13.0"), "answers": ANSWERS,
                     "usage": {"input_tokens": self.tokens, "output_tokens": 20}}, ""

    @property
    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


def run_args(files, *extra):
    questions, records, out = files
    return ["run", "--questions", str(questions), "--input", str(records), "--text-col", "текст", "--id-col", "id",
            "--out", str(out), "--label", "проба", *extra]


# ---------- вопросы ----------

def test_questions_comments_dropped_and_thresholds_read(files):
    questions, thresholds = jev.load_questions(files[0])
    assert set(questions) == {"жалоба", "тема", "сила"}
    assert thresholds["жалоба"]["действовать"] == 0.9


@pytest.mark.parametrize("broken, word", [
    ({"q": {"type": "bool", "instructions": "x"}}, "допустимы noul, choice, score"),
    ({"q": {"type": "choice", "instructions": "x", "criteria": {"один": "a"}}}, "от 2 до 255"),
    ({"q": {"type": "score", "instructions": "x", "criteria": [str(i) for i in range(11)]}}, "от 2 до 10"),
    ({"q": {"type": "noul", "instructions": "Это {{НИША}}?"}}, "{{...}}"),
    ({"q": {"type": "noul", "instructions": ""}}, "пустое поле"),
    ({"_пороги": {"нет_такого": {"действовать": 0.9}}, "q": {"type": "noul", "instructions": "x"}}, "нет в файле"),
    ({"_только": "комментарий"}, "нет ни одного вопроса"),
])
def test_bad_questions_rejected(tmp_path, broken, word):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        jev.load_questions(path)
    assert word in str(error.value)


def test_warning_when_choice_has_no_way_out():
    notes = jev.question_warnings({"q": {"type": "choice", "instructions": "x", "criteria": {"а": "1", "б": "2"}}})
    assert notes and "ничего из перечисленного" in notes[0]


def test_shuffle_keeps_options_and_is_repeatable():
    questions, _ = {k: v for k, v in QUESTIONS.items() if not k.startswith("_")}, None
    many = {"q": {"type": "choice", "instructions": "x", "criteria": {str(i): "о" for i in range(12)}}}
    first, again = jev.shuffle_choices(many, 7), jev.shuffle_choices(many, 7)
    assert list(first["q"]["criteria"]) == list(again["q"]["criteria"])
    assert set(first["q"]["criteria"]) == set(many["q"]["criteria"]) and list(first["q"]["criteria"]) != list(many["q"]["criteria"])
    assert jev.shuffle_choices(questions, 7)["жалоба"] == questions["жалоба"]


# ---------- записи и прогноз ----------

def test_multiline_text_is_one_record_and_empty_skipped(files):
    records, empty = jev.read_records(files[1], ["текст"], "id", None)
    assert [rid for rid, _ in records] == ["1", "2", "4"] and empty == 1
    assert "\n" in records[0][1]


def test_duplicate_ids_rejected(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("id;текст\n1;а\n1;б\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        jev.read_records(path, ["текст"], "id", None)


def test_forecast_counts_questions_in_every_request():
    questions = {k: v for k, v in QUESTIONS.items() if not k.startswith("_")}
    one = jev.forecast([("1", "текст")], questions, 0.042)
    two = jev.forecast([("1", "текст"), ("2", "текст")], questions, 0.042)
    assert two["варианты"]["ожидаемо"]["токенов"] == 2 * one["варианты"]["ожидаемо"]["токенов"]
    assert one["варианты"]["с запасом"]["токенов"] > one["варианты"]["мало"]["токенов"] > jev.REQUEST_OVERHEAD_TOKENS


def test_plan_prints_forecast_without_network(files, capsys):
    questions, records, _ = files
    code = jev.main(["plan", "--questions", str(questions), "--input", str(records), "--text-col", "текст", "--id-col", "id"])
    out = capsys.readouterr().out
    assert code == 0 and "ПРОБНЫЙ РЕЖИМ" in out and "запросов: 3" in out


def test_too_long_record_rejected():
    questions = {"q": {"type": "noul", "instructions": "x"}}
    fc = jev.forecast([("1", "я" * 200000)], questions, 0.042)
    with pytest.raises(SystemExit):
        jev.check_limits(fc, "openrouter.ai")


# ---------- защита до отправки ----------

def test_run_without_yes_sends_nothing(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--budget-usd", "1"), send=fake)
    assert "--yes" in str(error.value) and not fake.calls


def test_run_without_budget_sends_nothing(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    with pytest.raises(SystemExit):
        jev.main(run_args(files, "--yes"), send=fake)
    assert not fake.calls


def test_run_without_key_sends_nothing_and_ignores_openrouter_variable(files, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake)
    assert jev.KEY_VAR in str(error.value) and not fake.calls


def test_foreign_host_rejected(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    monkeypatch.setenv(jev.BASE_VAR, "https://example.org/api")
    fake = Fake()
    with pytest.raises(SystemExit):
        jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake)
    assert not fake.calls


def test_forecast_above_budget_sends_nothing(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "0.000001"), send=fake)
    assert "больше потолка" in str(error.value) and not fake.calls


def test_contacts_block_run_until_confirmed(files, monkeypatch):
    questions, records, out = files
    records.write_text("id;текст\n1;Позвоните мне +7 (912) 345-67-89\n2;Пишите на ivan@example.com\n3;Без контактов\n", encoding="utf-8")
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake)
    assert "в 2 записях" in str(error.value) and not fake.calls
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1", "--contacts-ok"), send=fake) == 0


def test_openrouter_key_without_limit_rejected(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    monkeypatch.setenv(jev.BASE_VAR, "https://openrouter.ai/api")
    fake = Fake(key_info={"limit": None, "limit_remaining": None})
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake)
    assert "нет лимита" in str(error.value) and not fake.posts


def test_openrouter_key_with_limit_accepted_and_model_pinned(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    monkeypatch.setenv(jev.BASE_VAR, "https://openrouter.ai/api")
    fake = Fake(key_info={"limit": 5, "limit_remaining": 5})
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake) == 0
    assert fake.posts[0][1] == "https://openrouter.ai/api/v1/systemone" and fake.posts[0][2]["model"] == "typesafe/jev-1.13"


def test_env_file_gives_only_client_variables(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"ЧУЖОЙ_СЕКРЕТ=не трогать\nOPENROUTER_API_KEY=личный\n{jev.KEY_VAR}={KEY}\n", encoding="utf-8")
    import os
    try:
        jev.load_env_file(env)
        assert os.environ[jev.KEY_VAR] == KEY and "ЧУЖОЙ_СЕКРЕТ" not in os.environ and "OPENROUTER_API_KEY" not in os.environ
    finally:
        os.environ.pop(jev.KEY_VAR, None)             # файл пишет в окружение мимо monkeypatch — убрать за собой


# ---------- прогон ----------

def test_run_writes_reads_back_and_resumes(files, monkeypatch, capsys):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake) == 0
    folder = files[2] / "проба"
    lines = (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()
    summary = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert len(lines) == 3 == len(fake.posts) == summary["rows_in_csv"] == summary["sent_now"]
    assert summary["input_tokens"] == 1200 and summary["model_answered"] == ["jev-1.13.0"]
    assert fake.posts[0][2]["model"] == "jev-1.13.0" and set(fake.posts[0][2]["questions"]) == {"жалоба", "тема", "сила"}
    table = (folder / "results.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(table) == 4 and "жалоба.зона" in table[0] and "действовать" in table[1]
    assert KEY not in capsys.readouterr().out and KEY not in (folder / "run.json").read_text(encoding="utf-8")

    again = Fake()
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=again) == 0
    assert not again.posts and len((folder / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_budget_stops_before_ceiling_even_when_records_cost_more_than_forecast(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake(tokens=1_000_000)                      # каждая запись стоит 0,042 $ — в сотни раз дороже прогноза
    code = jev.main(run_args(files, "--yes", "--budget-usd", "0.1", "--workers", "4"), send=fake)
    summary = json.loads((files[2] / "проба" / "run.json").read_text(encoding="utf-8"))
    assert code == 3 and "потолок" in summary["stopped"]
    assert summary["usd"] <= 0.1 and len(fake.posts) == 1


def test_auth_error_stops_without_retry_and_hides_key(files, monkeypatch, capsys):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake(status=401)
    code = jev.main(run_args(files, "--yes", "--budget-usd", "1", "--workers", "1"), send=fake)
    out = capsys.readouterr().out
    assert code == 3 and len(fake.posts) == 1 and "ключ не принят" in out and KEY not in out


def test_zero_written_is_not_success(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=Fake(status=422)) == 3
    assert (files[2] / "проба" / "results.jsonl").read_text(encoding="utf-8") == ""


def test_temporary_error_is_retried_then_succeeds():
    fake, naps = Fake(script=[429, 529, 200]), []
    data, unknown = jev.call(fake, "https://api.typesafe.ai/v1/systemone", KEY, {"model": "m", "questions": {}}, sleep=naps.append)
    assert data["answers"] and len(fake.posts) == 3 and len(naps) == 2 and unknown == 0      # 429 и 529 денег не списывают


def test_temporary_error_gives_up_after_attempts():
    fake = Fake(status=503)
    with pytest.raises(jev.Stop) as error:
        jev.call(fake, "https://api.typesafe.ai/v1/systemone", KEY, {}, attempts=3, sleep=lambda s: None)
    assert len(fake.posts) == 3 and error.value.unconfirmed == 3                              # сбой сервера мог списать деньги


# ---------- разбор ответа и сверка ----------

def test_flatten_and_zone():
    yes = jev.flatten({"type": "noul", "noul": 0.95})
    assert yes["value"] == "да" and yes["p_top"] == 0.95 and yes["confidence"] == 0.9
    no = jev.flatten({"type": "noul", "noul": 0.2})
    assert no["value"] == "нет" and no["p_top"] == 0.8
    assert jev.flatten(ANSWERS["тема"])["p_top"] == 0.8 and jev.flatten(ANSWERS["сила"])["value"] == "1"
    rule = {"действовать": 0.9, "проверить": 0.7}
    assert [jev.zone(p, rule) for p in (0.95, 0.75, 0.6)] == ["действовать", "проверить", "человеку"] and jev.zone(0.99, None) == ""


def result(rid, p_yes, level="1"):
    probabilities = {"0": 0.1, "1": 0.1, "2": 0.1}
    probabilities[level] = 0.8
    return rid, {"id": rid, "answers": {"жалоба": {"type": "noul", "noul": p_yes},
                                         "сила": {"type": "score", "score": float(level), "probabilities": probabilities, "confidence": 0.7}}}


def test_evaluate_counts_agreement_classes_bins_and_thresholds():
    results = dict([result("1", 0.95), result("2", 0.92, "0"), result("3", 0.6), result("4", 0.1), result("5", 0.3)])
    gold = [{"id": "1", "жалоба": "да", "сила": "1"}, {"id": "2", "жалоба": "1", "сила": "0"}, {"id": "3", "жалоба": "нет", "сила": "1"},
            {"id": "4", "жалоба": "false", "сила": "2"}, {"id": "5", "жалоба": "да", "сила": ""}, {"id": "6", "жалоба": "да", "сила": "1"}]
    report = jev.evaluate(results, gold, "id")
    complaint = report["жалоба"]
    assert complaint["сверено"] == 5 and complaint["совпало"] == 3                 # записи 3 и 5 — ошибки, записи 6 нет в результатах
    assert complaint["классы"]["да"] == {"в эталоне": 3, "найдено из них": 2, "названо моделью": 3, "из них верно": 2}
    assert complaint["корзины"]["0.9–1.0"] == {"ответов": 3, "верных": 3}
    assert complaint["пороги"]["0.9"] == {"решено без человека": 3, "доля": 0.6, "ошибок среди них": 0}
    assert complaint["пороги"]["0.5"]["ошибок среди них"] == 2
    strength = report["сила"]
    assert strength["сверено"] == 4 and strength["совпало"] == 3 and set(strength["классы"]) == {"0", "1", "2"}   # уровни не превращаются в «да/нет»


def test_compare_counts_changed_answers():
    first, second = dict([result("1", 0.9), result("2", 0.6)]), dict([result("1", 0.8), result("2", 0.4)])
    diff = jev.compare(first, second)["жалоба"]
    assert diff["сравнено"] == 2 and diff["ответ изменился"] == 1 and diff["сдвиг вероятности"] == 0.05


def test_wilson_interval_is_wide_on_small_samples():
    low, high = jev.wilson(63, 70)
    assert 0.80 < low < 0.82 and 0.95 < high < 0.96


# ---------- находки независимой проверки (Codex, 06.10.2026) ----------

@pytest.mark.parametrize("budget", ["nan", "inf", "0", "-1"])
def test_budget_must_be_finite_and_positive(files, monkeypatch, budget):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", budget), send=fake)
    assert "больше нуля" in str(error.value) and not fake.calls


@pytest.mark.parametrize("price", ["0", "0.01", "nan", "-1"])
def test_price_cannot_be_lowered(files, monkeypatch, price):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1", "--price-per-mtok", price), send=fake)
    assert "не может быть ниже" in str(error.value) and not fake.calls


@pytest.mark.parametrize("model", ["jev-latest", "~typesafe/jev-latest", "jev-preview", "gpt-5", "typesafe/jev-router"])
def test_model_alias_or_foreign_model_rejected(files, monkeypatch, model):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1", "--model", model), send=fake)
    assert "точная версия" in str(error.value) and not fake.calls


def test_exact_other_version_accepted(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    fake = Fake()
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1", "--model", "jev-1.14.0"), send=fake) == 0
    assert fake.posts[0][2]["model"] == "jev-1.14.0"


def test_small_forecast_is_not_rounded_to_zero():
    fc = jev.forecast([("1", "а")], {"q": {"type": "noul", "instructions": "x"}}, 0.042)
    assert 0 < fc["варианты"]["с запасом"]["usd"] < 0.0001


def test_timeouts_stop_run_and_count_as_possibly_billed(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    monkeypatch.setattr(jev.time, "sleep", lambda seconds: None)
    calls = []

    def timeout(method, url, key, payload=None, timeout=60):
        calls.append(method)
        return 0, None, "TimeoutError"
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=timeout) == 3
    summary = json.loads((files[2] / "проба" / "run.json").read_text(encoding="utf-8"))
    assert len(calls) == jev.ATTEMPTS and summary["usd"] == 0 and summary["usd_unconfirmed"] > 0 and summary["sent_now"] == 0


@pytest.mark.parametrize("body", [
    {"model": "jev-1.13.0", "usage": {"input_tokens": 400}},
    {"model": "jev-1.13.0", "answers": None, "usage": {"input_tokens": 400}},
    {"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 400}},
    {"model": "jev-1.13.0", "answers": {"жалоба": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 400}},
])
def test_ok_status_without_all_answers_stops_without_retry_or_row(files, monkeypatch, body):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    calls = []

    def broken(method, url, key, payload=None, timeout=60):
        calls.append(method)
        return 200, body, ""
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=broken) == 3
    folder = files[2] / "проба"
    assert len(calls) == 1 and (folder / "results.jsonl").read_text(encoding="utf-8") == ""
    assert json.loads((folder / "run.json").read_text(encoding="utf-8"))["usd_unconfirmed"] > 0


def test_redirect_is_refused():
    assert jev.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.org/v1/systemone") is None


def test_transport_cuts_key_out_of_server_text(monkeypatch):
    import io
    import urllib.error

    def echo_key(request, timeout):
        assert request.get_header("Authorization") == f"Bearer {KEY}"
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(
            json.dumps({"detail": f"bad header: Bearer {KEY}"}, ensure_ascii=False).encode("utf-8")))
    monkeypatch.setattr(jev, "open_url", echo_key)
    status, data, text = jev.transport("POST", "https://api.typesafe.ai/v1/systemone", KEY, {"model": "m"})
    assert status == 401 and KEY not in text and KEY not in json.dumps(data, ensure_ascii=False) and "<ключ>" in text


def test_redirect_stops_the_call_without_retry(monkeypatch):
    import io
    import urllib.error

    def redirect(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 302, "Found", {"Location": "https://example.org/"}, io.BytesIO(b""))
    monkeypatch.setattr(jev, "open_url", redirect)
    naps = []
    with pytest.raises(jev.Stop) as error:
        jev.call(jev.transport, "https://api.typesafe.ai/v1/systemone", KEY, {"model": "m", "questions": {}}, sleep=naps.append)
    assert "перенаправление" in str(error.value) and not naps


def test_resume_survives_torn_last_line(files, monkeypatch, capsys):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=Fake()) == 0
    path = files[2] / "проба" / "results.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n" + lines[1] + "\n" + lines[2][:40], encoding="utf-8")       # обрыв посреди третьей строки
    again = Fake()
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=again) == 0
    restored = path.read_text(encoding="utf-8").splitlines()
    assert len(again.posts) == 1 and len(restored) == 3 and all(json.loads(line)["answers"] for line in restored)
    assert "оборванных строк: 1" in capsys.readouterr().out


def test_second_run_with_same_label_is_refused_and_lock_is_released(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    folder = files[2] / "проба"
    folder.mkdir(parents=True)
    (folder / "run.lock").write_text("", encoding="utf-8")
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake)
    assert "уже идёт" in str(error.value) and not fake.calls
    (folder / "run.lock").unlink()
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake) == 0 and not (folder / "run.lock").exists()
    with pytest.raises(SystemExit):                                                         # отказ после захвата тоже снимает замок
        jev.main(run_args(files, "--yes", "--budget-usd", "0.0000001", "--label", "мало"), send=fake)
    assert not (files[2] / "мало" / "run.lock").exists()


def test_phone_of_any_country_blocks_but_norm_numbers_do_not():
    records = [("1", "Call me at +1 212 555 0199"), ("2", "тел +44 20 7946 0958"), ("3", "кратность воздухообмена сп 60.13330.2020"),
               ("4", "гост 30494-2011, цена 105 500 рублей"), ("5", "8 (912) 345-67-89"), ("6", "кив 125 1000 мм"),
               ("7", "заказ от 22.06.2026 в 12:30")]
    assert jev.contact_ids(records) == ["1", "2", "5"]


def test_weak_choice_answer_is_not_lost_from_probability_bins():
    weak = {"id": "1", "answers": {"тема": {"type": "choice", "choice": "монтаж", "confidence": 0.1,
                                            "probabilities": {"монтаж": 0.4, "прибор": 0.35, "другое": 0.25}}}}
    report = jev.evaluate({"1": weak}, [{"id": "1", "тема": "прибор"}], "id")
    assert report["тема"]["корзины"]["0.0–0.5"] == {"ответов": 1, "верных": 0}
    assert sum(item["ответов"] for item in report["тема"]["корзины"].values()) == report["тема"]["сверено"]


# ---------- находки повторной проверки (Codex, 06.10.2026) ----------

def good_body(payload, **usage):
    return {"model": payload["model"], "answers": dict(ANSWERS), "usage": {"input_tokens": 400, "output_tokens": 20, **usage}}


@pytest.mark.parametrize("base", ["https://api.typesafe.ai:444", "https://api.typesafe.ai/anything", "https://user@api.typesafe.ai",
                                  "https://openrouter.ai", "https://openrouter.ai/api/v1", "http://api.typesafe.ai"])
def test_only_two_exact_base_addresses_are_allowed(files, monkeypatch, base):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    monkeypatch.setenv(jev.BASE_VAR, base)
    fake = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=fake)
    assert "не разрешён" in str(error.value) and not fake.calls


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -1.0, "0.001", True])
def test_bad_cost_in_answer_stops_run_and_keeps_budget_arithmetic_sane(files, monkeypatch, cost):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    monkeypatch.setenv(jev.BASE_VAR, "https://openrouter.ai/api")
    calls = []

    def send(method, url, key, payload=None, timeout=60):
        calls.append(method)
        if method == "GET":
            return 200, {"data": {"limit": 5, "limit_remaining": 5}}, ""
        return 200, good_body(payload, cost=cost), ""
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=send) == 3
    summary = json.loads((files[2] / "проба" / "run.json").read_text(encoding="utf-8"))
    assert calls.count("POST") == 1 and summary["sent_now"] == 0 and summary["usd"] == 0 and 0 < summary["usd_unconfirmed"] < 1


def test_transport_rejects_nan_and_cuts_escaped_key(monkeypatch):
    import io

    class Reply(io.BytesIO):
        status = 200

    def nan_body(request, timeout):
        return Reply(b'{"answers": {"q": {"type": "noul", "noul": NaN}}, "usage": {"input_tokens": 1}}')
    monkeypatch.setattr(jev, "open_url", nan_body)
    status, data, text = jev.transport("POST", "https://api.typesafe.ai/v1/systemone", KEY, {})
    assert status == 200 and data is None

    escaped = json.dumps({"answers": {"q": {"type": "choice", "choice": KEY, "probabilities": {KEY: 1.0}}}}, ensure_ascii=True)
    assert KEY not in escaped                                               # в сыром тексте ключа нет — он появится после разбора
    monkeypatch.setattr(jev, "open_url", lambda request, timeout: Reply(escaped.encode("ascii")))
    status, data, text = jev.transport("POST", "https://api.typesafe.ai/v1/systemone", KEY, {})
    assert data is not None and KEY not in json.dumps(data, ensure_ascii=False) and "<ключ>" in json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize("name, bad", [
    ("жалоба", {"type": "noul", "noul": 1.5}),
    ("жалоба", {}),
    ("жалоба", {"type": "choice", "choice": "монтаж", "probabilities": {"монтаж": 1.0}, "confidence": 1}),
    ("тема", {"type": "choice", "choice": "доставка", "probabilities": {"доставка": 1.0}, "confidence": 1}),
    ("тема", {"type": "choice", "choice": "монтаж", "probabilities": {}, "confidence": 1}),
    ("сила", {"type": "score", "score": 5.0, "probabilities": {"7": 1.0}, "confidence": 1}),
    ("сила", {"type": "score", "probabilities": {"1": 1.0}, "confidence": 1}),
])
def test_malformed_answer_is_not_written_as_result(files, monkeypatch, name, bad):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    calls = []

    def send(method, url, key, payload=None, timeout=60):
        calls.append(method)
        body = good_body(payload)
        body["answers"][name] = bad
        return 200, body, ""
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=send) == 3
    assert len(calls) == 1 and (files[2] / "проба" / "results.jsonl").read_text(encoding="utf-8") == ""


def test_answer_without_usage_is_not_written(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)

    def send(method, url, key, payload=None, timeout=60):
        return 200, {"model": payload["model"], "answers": dict(ANSWERS)}, ""
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=send) == 3
    assert (files[2] / "проба" / "results.jsonl").read_text(encoding="utf-8") == ""


def test_other_model_in_answer_stops_run(files, monkeypatch, capsys):
    monkeypatch.setenv(jev.KEY_VAR, KEY)

    def send(method, url, key, payload=None, timeout=60):
        body = good_body(payload)
        body["model"] = "jev-2.0.0"
        return 200, body, ""
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=send) == 3
    assert "ответила модель" in capsys.readouterr().out
    assert (files[2] / "проба" / "results.jsonl").read_text(encoding="utf-8") == ""


def test_dated_snapshot_of_asked_model_is_accepted():
    payload = {"model": "typesafe/jev-1.13", "questions": {}}
    body = {"model": "typesafe/jev-1.13-20260917", "answers": dict(ANSWERS), "usage": {"input_tokens": 400}}
    assert jev.response_problem(payload, body) == ""


def test_changed_questions_under_same_label_are_refused(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=Fake()) == 0
    changed = json.loads(files[0].read_text(encoding="utf-8"))
    changed["жалоба"]["instructions"] = "Автор жалуется?"
    files[0].write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    again = Fake()
    with pytest.raises(SystemExit) as error:
        jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=again)
    assert "новый --label" in str(error.value) and not again.calls
    with pytest.raises(SystemExit):                                                          # перестановка вариантов — тоже другие вопросы
        jev.main(run_args(files, "--yes", "--budget-usd", "1", "--shuffle-choices", "1"), send=again)
    assert not again.calls
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1", "--label", "вторая попытка"), send=again) == 0


def test_table_does_not_mix_rows_of_other_questions(tmp_path):
    path = tmp_path / "results.jsonl"
    rows = [{"id": "1", "hash": "новый", "model": "m", "answers": {"жалоба": {"type": "noul", "noul": 0.9}}},
            {"id": "2", "hash": "старый", "model": "m", "answers": {"жалоба": {"type": "noul", "noul": 0.1}}}]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    assert jev.write_csv(path, tmp_path / "results.csv", {}, {"1": "новый", "2": "новый"}) == 1
    assert len((tmp_path / "results.csv").read_text(encoding="utf-8-sig").splitlines()) == 2


def test_budget_holds_with_several_workers_when_each_record_costs_its_upper_bound(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    questions, _ = jev.load_questions(files[0])
    records, _ = jev.read_records(files[1], ["текст"], "id", None)
    bounds = [jev.request_bound_usd({"model": "jev-1.13.0", "state": state, "questions": questions}, "api.typesafe.ai", 0.042)
              for _, state in records]
    budget = max(bounds) * jev.ATTEMPTS * 1.05           # хватает на одну запись со всеми её повторами, на вторую — уже нет
    calls = []

    def send(method, url, key, payload=None, timeout=60):
        calls.append(method)
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return 200, good_body(payload, input_tokens=size + jev.OVERHEAD_BOUND_TOKENS), ""     # ровно верхняя граница
    code = jev.main(run_args(files, "--yes", "--budget-usd", repr(budget), "--workers", "8"), send=send)
    summary = json.loads((files[2] / "проба" / "run.json").read_text(encoding="utf-8"))
    assert code == 3 and "потолок" in summary["stopped"]
    assert summary["usd"] + summary["usd_unconfirmed"] <= budget and len(calls) == 1 and summary["sent_now"] == 1


def test_upper_bound_covers_real_price_of_ordinary_request():
    payload = {"model": "jev-1.13.0", "state": "Прибор шумит", "questions": {"q": {"type": "noul", "instructions": "Автор недоволен?"}}}
    bound = jev.request_bound_usd(payload, "api.typesafe.ai", 0.042)
    assert bound >= 296 * 0.042 / 1e6 * 5                                                    # пример документации: 296 токенов
    huge = {"model": "jev-1.13.0", "state": "я" * 500000, "questions": {}}
    assert jev.request_bound_usd(huge, "openrouter.ai", 0.042) == 32000 * 0.042 / 1e6        # длиннее предела сервис не примет


# ---------- находки третьей проверки (Codex, 06.10.2026) ----------

def test_choice_without_probability_of_chosen_option_is_not_written(files, monkeypatch):
    monkeypatch.setenv(jev.KEY_VAR, KEY)

    def send(method, url, key, payload=None, timeout=60):
        body = good_body(payload)
        body["answers"]["тема"] = {"type": "choice", "choice": "монтаж", "probabilities": {"прибор": 1.0}, "confidence": 0.1}
        return 200, body, ""
    assert jev.main(run_args(files, "--yes", "--budget-usd", "1"), send=send) == 3
    assert (files[2] / "проба" / "results.jsonl").read_text(encoding="utf-8") == ""


def test_flatten_never_borrows_probability_of_another_option():
    flat = jev.flatten({"type": "choice", "choice": "монтаж", "probabilities": {"прибор": 1.0}, "confidence": 0.1})
    assert flat["value"] == "монтаж" and flat["p_top"] == 0.0


@pytest.mark.parametrize("asked, answered, accepted", [
    ("jev-1.13.0", "jev-1.13.0", True),
    ("typesafe/jev-1.13", "typesafe/jev-1.13-20260917", True),
    ("typesafe/jev-1.13", "typesafe/jev-1.13.1", False),
    ("typesafe/jev-1.13", "typesafe/jev-1.130", False),
    ("jev-1.13.0", "jev-1.13.0-preview", False),
    ("jev-1.13.0", "", False),
])
def test_answered_model_must_be_asked_version_or_its_dated_snapshot(asked, answered, accepted):
    body = {"model": answered, "answers": dict(ANSWERS), "usage": {"input_tokens": 400}}
    assert (jev.response_problem({"model": asked, "questions": {}}, body) == "") is accepted


def test_overpriced_answer_is_kept_but_run_stops_and_says_so(files, monkeypatch, capsys):
    monkeypatch.setenv(jev.KEY_VAR, KEY)
    monkeypatch.setenv(jev.BASE_VAR, "https://openrouter.ai/api")
    calls = []

    def send(method, url, key, payload=None, timeout=60):
        calls.append(method)
        if method == "GET":
            return 200, {"data": {"limit": 5, "limit_remaining": 5}}, ""
        return 200, good_body(payload, cost=0.01), ""                       # сервис взял в сотни раз больше документированного
    assert jev.main(run_args(files, "--yes", "--budget-usd", "0.002", "--workers", "8"), send=send) == 3
    summary = json.loads((files[2] / "проба" / "run.json").read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    assert calls.count("POST") == 1 and summary["sent_now"] == 1 and summary["usd"] == 0.01      # оплаченный ответ сохранён
    assert "дороже документированной цены" in summary["stopped"] and "потолок уже превышен" in summary["stopped"] and "ПРОГОН ОСТАНОВЛЕН" in out
