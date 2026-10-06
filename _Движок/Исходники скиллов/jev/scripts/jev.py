# -*- coding: utf-8 -*-
"""Клиент JEV (TypeSafe AI) — модели-решателя: по тексту записи отвечает на вопросы «да/нет», «выбор», «шкала».

Пять команд:
    python jev.py check --questions вопросы.json
        проверить файл вопросов, ничего не отправляя
    python jev.py ping [--yes]
        проверка связи: один зашитый в код запрос без повторов — работает ли ключ и отвечает ли модель
    python jev.py plan  --questions вопросы.json --input записи.csv --text-col текст [--id-col id] [--limit N]
        пробный режим: число запросов, прогноз токенов и цены, пример запроса; в сеть не ходит
    python jev.py run   --questions вопросы.json --input записи.csv --text-col текст --out <папка> --label <имя>
                        --budget-usd 1 --yes
        платный прогон; без --yes и --budget-usd не стартует
    python jev.py eval  --results <папка прогона>/results.jsonl --gold эталон.csv [--compare другой/results.jsonl]
        сверка с человеческой разметкой: совпадение по вопросам и классам, честность вероятностей, таблица порогов

Что клиент соблюдает сам, а не по просьбе в описании:
- ключ берётся только из переменной TYPESAFE_API_KEY либо из переменной, названной явно в --key-var (в окружении
  или в файле --env-file), не печатается, вырезается из текста и из разобранного JSON ответов сервиса и не
  попадает в ошибки и файлы; сам по себе клиент переменные OPENROUTER_API_KEY не читает;
- адрес — ровно https://api.typesafe.ai или https://openrouter.ai/api, без другого пути, порта и имени в адресе;
  перенаправления не выполняются; прогон с ключом OpenRouter без лимита расходов отклоняется до отправки —
  кроме малого прогона с явным флагом --key-without-limit и потолком не выше 0,5 $ (проверка связи ping —
  один зашитый запрос без повторов — допускает такой ключ с предупреждением);
- цена и число запросов печатаются до первого запроса; потолок --budget-usd — конечное число больше нуля, цену
  нельзя занизить; под каждый запрос бронируется верхняя граница его цены на все повторы (токенов не больше, чем
  байт в запросе, и не больше предела входа), поэтому при цене не выше документированной прогон останавливается,
  не доходя до потолка. Цену запроса клиент узнаёт только из ответа: если сервис взял больше границы, прогон
  останавливается после первого такого ответа — жёсткий предел здесь даёт только баланс счёта и лимит ключа;
- повторяются только временные сбои (429, 529, 5xx, обрыв сети); попытка без принятого ответа считается возможно
  оплаченной; ошибка ключа, баланса, доступа, формата запроса останавливает прогон;
- успешный ответ проверяется до записи: ответы на все вопросы нужного типа с вероятностями от 0 до 1, число
  входных токенов, конечная неотрицательная стоимость, та же версия модели; иначе прогон останавливается, строка
  результата не пишется;
- модель — только точная версия, псевдонимы latest и preview не принимаются;
- записи с телефоном или адресом почты не отправляются, пока человек не подтвердил, что это не контакты людей
  (имена и адреса шаблон не ловит — их убирают до прогона);
- результат пишется по мере работы; повторный запуск с тем же --label продолжает с места остановки, оборванная
  строка отбрасывается; другие вопросы или другая модель под тем же именем отклоняются — ответы не смешиваются;
  второй одновременный запуск с тем же именем отклоняется файлом run.lock;
- ноль записанных строк — код выхода 2, остановка по потолку или ошибке — код 3.

Только стандартная библиотека Python 3.10+. Цена и пределы — docs.typesafe.ai/models на 05.10.2026.
"""
import argparse, csv, hashlib, io, json, math, os, random, re, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

PRICE_PER_MTOK_USD = 0.042                 # входные токены; выход бесплатный
KEY_VAR, BASE_VAR = "TYPESAFE_API_KEY", "TYPESAFE_BASE_URL"
DEFAULT_BASE = "https://api.typesafe.ai"
BASES = {"https://api.typesafe.ai": "api.typesafe.ai", "https://openrouter.ai/api": "openrouter.ai"}   # адрес → имя узла
HOSTS = {                                  # разрешённые адреса: закреплённая версия модели и предел входа в токенах
    "api.typesafe.ai": {"model": "jev-1.13.0", "context": 64000},
    "openrouter.ai": {"model": "typesafe/jev-1.13", "context": 32000},
}
STATE_PLUS_QUESTION_LIMIT = 32000          # запись вместе с самым длинным вопросом
REQUEST_OVERHEAD_TOKENS = 300              # постоянная часть запроса: короткая фраза и один вопрос в примере = 296
CHARS_PER_TOKEN = {"мало": 3.0, "ожидаемо": 2.0, "с запасом": 1.5}   # для кириллицы токенизатор не описан
STOP_CODES = {400, 401, 402, 403, 404, 413, 422}
RETRY_CODES = {408, 429, 500, 502, 503, 504, 524, 529}
REJECTED_UNBILLED = {429, 529}             # отказ до обработки: лимит частоты и перегрузка денег не списывают
ATTEMPTS = 4
UNLIMITED_KEY_BUDGET_CAP = 0.5             # с ключом OpenRouter без лимита прогон допускается только при таком потолке, $
OVERHEAD_BOUND_TOKENS = 2000               # запас на постоянную часть запроса при расчёте верхней границы цены
MODEL_ID = re.compile(r"^(typesafe/)?jev-\d+(\.\d+)*(-\d{8})?$")   # точная версия; псевдонимы latest и preview не годятся
# телефон — 10–15 цифр подряд через пробел, дефис или скобки (любая страна); точка разделителем не считается,
# иначе под шаблон попадают номера нормативов вроде «СП 60.13330.2020»
CONTACT = re.compile(r"(?<![\w+])\+?\d(?:[\s\-()]{0,3}\d){9,14}(?!\w)|[\w.+-]+@[\w-]+\.[\w.-]+")
NO_MATCH_WORDS = ("друг", "ничего", "нет_", "net_", "none", "other")
BINS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0001))


class Stop(Exception):
    """Ошибка, после которой прогон не продолжают: ключ, баланс, доступ, формат запроса или ответа.

    unconfirmed — число попыток, по которым неизвестно, списал ли сервис деньги (таймаут, обрыв, сбой сервера).
    """
    def __init__(self, message: str, unconfirmed: int = 0):
        super().__init__(message)
        self.unconfirmed = unconfirmed


# ---------- вопросы и записи ----------

def load_questions(path) -> tuple[dict, dict]:
    """Файл вопросов → (вопросы для отправки, пороги). Ключи с «_» в начале — комментарии; `_пороги` — пороги."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise SystemExit("jev: файл вопросов — это объект «имя вопроса → вопрос»")
    thresholds = raw.get("_пороги") or {}
    questions = {k: v for k, v in raw.items() if not k.startswith("_")}
    problems = []
    if not questions:
        problems.append("в файле нет ни одного вопроса")
    if "{{" in json.dumps(questions, ensure_ascii=False):
        problems.append("остались незаполненные места {{...}}")
    for name, q in questions.items():
        kind, criteria = (q or {}).get("type"), (q or {}).get("criteria")
        if kind not in ("noul", "choice", "score"):
            problems.append(f"{name}: тип «{kind}» — допустимы noul, choice, score")
            continue
        if not q.get("instructions"):
            problems.append(f"{name}: пустое поле instructions")
        if kind == "noul" and criteria is not None and (not isinstance(criteria, dict) or set(criteria) - {"true", "false"}):
            problems.append(f"{name}: у noul критерии — только ключи true и false")
        if kind == "choice" and (not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255):
            problems.append(f"{name}: у choice нужен словарь от 2 до 255 вариантов")
        if kind == "score" and (not isinstance(criteria, list) or not 2 <= len(criteria) <= 10):
            problems.append(f"{name}: у score нужен список от 2 до 10 уровней")
    for name, rule in thresholds.items():
        if name not in questions:
            problems.append(f"_пороги: вопроса «{name}» нет в файле")
        elif not isinstance(rule, dict):
            problems.append(f"_пороги.{name}: нужен объект с ключами «действовать» и «проверить»")
        elif not 0.5 <= float(rule.get("проверить", 0.5)) <= float(rule.get("действовать", 1)) <= 1:
            problems.append(f"_пороги.{name}: нужно 0,5 ≤ проверить ≤ действовать ≤ 1")
    if problems:
        raise SystemExit("jev: файл вопросов не принят:\n  - " + "\n  - ".join(problems))
    return questions, thresholds


def question_warnings(questions: dict) -> list[str]:
    notes = []
    for name, q in questions.items():
        if q["type"] == "choice" and not any(w in key.lower() for key in q["criteria"] for w in NO_MATCH_WORDS):
            notes.append(f"{name}: нет варианта «ничего из перечисленного» — модель обязана выбрать один из данных")
        if q["type"] == "noul" and not q.get("criteria"):
            notes.append(f"{name}: не описано, что считать «да» и что «нет»")
    return notes


def shuffle_choices(questions: dict, seed: int) -> dict:
    """Переставить варианты у вопросов «выбор»: проверка, не тянется ли модель к первому варианту."""
    out = {}
    for name, q in questions.items():
        if q["type"] == "choice":
            items = list(q["criteria"].items())
            random.Random(f"{seed}:{name}").shuffle(items)
            q = {**q, "criteria": dict(items)}
        out[name] = q
    return out


def read_records(path, text_cols, id_col, limit) -> tuple[list[tuple[str, object]], int]:
    """Записи из CSV (разделитель «;», «,» или табуляция) или JSONL со строками {"id", "state"}. → (записи, пустых)."""
    path, rows, empty = Path(path), [], 0
    if path.suffix.lower() == ".jsonl":
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if line.strip():
                item = json.loads(line)
                rows.append((str(item.get("id", n)), item.get("state")))
    else:
        text = path.read_text(encoding="utf-8-sig")
        head = text.split("\n", 1)[0]
        delimiter = max(";,\t", key=head.count)
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)   # текст записи бывает многострочным
        missing = [c for c in text_cols + ([id_col] if id_col else []) if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"jev: в {path.name} нет колонок: {', '.join(missing)}; есть: {', '.join(reader.fieldnames or [])}")
        for n, row in enumerate(reader, 1):
            state = (row[text_cols[0]] or "").strip() if len(text_cols) == 1 else {c: (row[c] or "").strip() for c in text_cols}
            rows.append((str(row[id_col]).strip() if id_col else str(n), state))
    kept = []
    for rid, state in rows:
        if state in ("", None) or (isinstance(state, dict) and not any(state.values())):
            empty += 1
        else:
            kept.append((rid, state))
    ids = [rid for rid, _ in kept]
    if len(set(ids)) != len(ids):
        raise SystemExit("jev: идентификаторы записей повторяются — укажите --id-col с уникальными значениями")
    return (kept[:limit] if limit else kept), empty


def as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def record_hash(model: str, state, questions: dict) -> str:
    return hashlib.sha256(json.dumps([model, state, questions], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# ---------- прогноз ----------

def tokens(chars: int, chars_per_token: float) -> int:
    return math.ceil(chars / chars_per_token)


def forecast(records, questions: dict, price: float) -> dict:
    """Прогноз до запуска: платится весь вход — запись и все вопросы — в каждом запросе."""
    q_chars = len(json.dumps(questions, ensure_ascii=False))
    longest_q = max(len(json.dumps(q, ensure_ascii=False)) for q in questions.values())
    state_chars = [len(as_text(state)) for _, state in records]
    out = {"запросов": len(records), "символов_в_вопросах": q_chars, "символов_в_записи_в_среднем":
           round(sum(state_chars) / len(state_chars)) if state_chars else 0, "варианты": {}}
    for label, cpt in CHARS_PER_TOKEN.items():
        total = sum(REQUEST_OVERHEAD_TOKENS + tokens(q_chars + c, cpt) for c in state_chars)
        out["варианты"][label] = {"токенов": total, "usd": total * price / 1e6}   # без округления: по нему сверяется потолок
    worst = CHARS_PER_TOKEN["с запасом"]
    out["самый_большой_запрос_токенов"] = REQUEST_OVERHEAD_TOKENS + tokens(q_chars + max(state_chars, default=0), worst)
    out["запись_и_длинный_вопрос_токенов"] = tokens(longest_q + max(state_chars, default=0), worst)
    return out


def print_forecast(fc: dict, host: str, model: str, price: float) -> None:
    print(f"запросов: {fc['запросов']} | модель: {model} | адрес: {host} | цена: {price} $ за млн входных токенов")
    print(f"в каждом запросе: вопросы {fc['символов_в_вопросах']} симв. + запись в среднем {fc['символов_в_записи_в_среднем']} симв.")
    for label, item in fc["варианты"].items():
        print(f"  прогноз «{label}» ({CHARS_PER_TOKEN[label]} симв. на токен): {item['токенов']:,} токенов".replace(",", " ")
              + f" ≈ {item['usd']:.4f} $")
    print("  прогноз — оценка: сколько токенов занимает русский текст, документация не говорит; факт — из ответа сервиса")


def check_limits(fc: dict, host: str) -> None:
    if fc["самый_большой_запрос_токенов"] > HOSTS[host]["context"]:
        raise SystemExit(f"jev: самая длинная запись с вопросами ≈ {fc['самый_большой_запрос_токенов']} токенов — "
                         f"больше предела {HOSTS[host]['context']} для {host}; сократите запись или уберите вопросы")
    if fc["запись_и_длинный_вопрос_токенов"] > STATE_PLUS_QUESTION_LIMIT:
        raise SystemExit("jev: запись вместе с самым длинным вопросом больше 32 тысяч токенов — сократите запись")


def contact_ids(records) -> list[str]:
    return [rid for rid, state in records if CONTACT.search(as_text(state))]


# ---------- сеть ----------

def load_env_file(path, key_var: str = KEY_VAR) -> None:
    """Из файла берутся только переменная ключа и адрес; остальные секреты файла не читаются в окружение."""
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        name, _, value = line.partition("=")
        if name.strip() in (key_var, BASE_VAR) and value.strip():
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def endpoint() -> tuple[str, str]:
    """Адрес сервиса — ровно один из двух разрешённых: другой путь, порт или имя пользователя в адресе не принимаются."""
    base = (os.environ.get(BASE_VAR) or DEFAULT_BASE).rstrip("/")
    if base not in BASES:
        raise SystemExit(f"jev: адрес из {BASE_VAR} не разрешён — допустимы только {' и '.join(BASES)}")
    return base, BASES[base]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправления не выполняются: иначе заголовок с ключом ушёл бы на адрес, который никто не проверял."""
    def redirect_request(self, *args, **kwargs):
        return None


def open_url(request, timeout: int):
    return urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)


def scrub(value, key: str):
    """Вырезать ключ из разобранного ответа: в JSON он может прийти в экранированном виде и пережить чистку текста."""
    if isinstance(value, str):
        return value.replace(key, "<ключ>")
    if isinstance(value, list):
        return [scrub(item, key) for item in value]
    if isinstance(value, dict):
        return {scrub(name, key): scrub(item, key) for name, item in value.items()}
    return value


def not_a_number(token: str):
    raise ValueError(f"в ответе стоит {token}")          # NaN и Infinity в деньгах и вероятностях недопустимы


def transport(method: str, url: str, key: str, payload=None, timeout: int = 60) -> tuple[int, object, str]:
    """Один HTTP-вызов → (код, разобранный JSON или None, начало текста ответа).

    Ключ вырезается и из текста ответа, и из разобранного JSON: сервер или прокси могут вернуть его в теле.
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with open_url(request, timeout) as response:
            status, text = response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return 0, None, type(error).__name__
    text = text.replace(key, "<ключ>") if key else text
    try:
        parsed = json.loads(text, parse_constant=not_a_number)
    except ValueError:
        return status, None, text[:300]
    return status, scrub(parsed, key) if key else parsed, text[:300]


def is_probability(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and -1e-6 <= value <= 1 + 1e-6


def answer_problem(question: dict, answer) -> str:
    """Что не так с ответом на вопрос; пустая строка — ответ соответствует вопросу."""
    kind = question.get("type")
    if not isinstance(answer, dict) or answer.get("type") != kind:
        return "тип ответа не совпал с типом вопроса"
    if kind == "noul":
        return "" if is_probability(answer.get("noul")) else "вероятность «да» не число от 0 до 1"
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or not probabilities or not all(is_probability(v) for v in probabilities.values()):
        return "нет распределения вероятностей или в нём не числа от 0 до 1"
    if kind == "choice":
        options = set(question.get("criteria") or {})
        if answer.get("choice") not in options or set(map(str, probabilities)) - options:
            return "выбран вариант, которого нет в вопросе"
        if answer["choice"] not in probabilities:
            return "у выбранного варианта нет вероятности"
        return ""
    levels = {str(i) for i in range(len(question.get("criteria") or []))}
    if set(map(str, probabilities)) - levels:
        return "уровень вне шкалы вопроса"
    score = answer.get("score")
    return "" if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score) else "нет оценки по шкале"


def response_problem(payload: dict, data) -> str:
    """Проверка успешного ответа до того, как он станет строкой результата и расходом: ответы, расход, версия модели."""
    questions = payload.get("questions") or {}
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict) or not answers or set(questions) - set(answers):
        return "в ответе нет ответов на все вопросы"
    for name, question in questions.items():
        problem = answer_problem(question, answers[name])
        if problem:
            return f"ответ на вопрос «{name}»: {problem}"
    usage = data.get("usage")
    tokens_in = usage.get("input_tokens") if isinstance(usage, dict) else None
    if not isinstance(tokens_in, int) or isinstance(tokens_in, bool) or tokens_in < 0:
        return "в ответе нет числа входных токенов — расход посчитать нельзя"
    cost = usage.get("cost")
    if cost is not None and not (isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0):
        return "стоимость в ответе — не конечное неотрицательное число"
    asked, answered = str(payload.get("model") or ""), str(data.get("model") or "")
    if asked and answered != asked and not re.fullmatch(re.escape(asked) + r"-\d{8}", answered):
        return f"ответила модель «{answered or 'не названа'}», а запрошена «{asked}»"
    return ""


def call(send, url: str, key: str, payload: dict, attempts: int = ATTEMPTS, sleep=None) -> tuple[dict, int]:
    """Запрос с повтором только временных сбоев → (ответ, число попыток с неизвестным списанием).

    Ошибка ключа, баланса, доступа, формата запроса и успешный ответ, не прошедший проверку, — Stop без повторов.
    Таймаут, обрыв и сбой сервера повторяются, но каждая такая попытка считается возможно оплаченной.
    """
    unknown = 0
    for attempt in range(1, attempts + 1):
        status, data, text = send("POST", url, key, payload)
        if status == 200:
            problem = response_problem(payload, data)
            if not problem:
                return data, unknown
            raise Stop(f"HTTP 200, но {problem} — прогон остановлен, строка результата не записана", unknown + 1)
        if status in STOP_CODES:
            hint = {401: "ключ не принят", 402: "на счёте не хватает средств", 403: "доступ закрыт или ключ не передан; "
                    "если в ответе страница Cloudflare — запись похожа на команду и отклонена защитой",
                    422: "сервис не принял формат запроса — проверьте вопросы командой check"}.get(status, "запрос отклонён")
            raise Stop(f"HTTP {status}: {hint} | {text[:200]}", unknown)
        if status not in RETRY_CODES and status != 0:
            raise Stop(f"HTTP {status}: неожиданный ответ, в том числе перенаправление — прогон остановлен | {text[:200]}", unknown + 1)
        if status not in REJECTED_UNBILLED:
            unknown += 1
        if attempt == attempts:
            raise Stop(f"HTTP {status}: сервис не ответил после {attempt} попыток | {text[:200]}", unknown)
        (sleep or time.sleep)(min(30, 2 ** attempt) + random.random())
    raise Stop("сервис не ответил", unknown)


def openrouter_key_guard(send, base: str, key: str, need_usd: float, allow_unlimited: bool = False) -> None:
    """У ключа OpenRouter должен стоять лимит расходов: ключ без лимита тратит весь счёт владельца.

    Владелец может явно разрешить ключ без лимита, но только для малого прогона — с потолком не выше
    UNLIMITED_KEY_BUDGET_CAP; большой прогон без лимита на ключе не стартует.
    """
    status, data, text = send("GET", base + "/v1/key", key)
    info = (data or {}).get("data") if isinstance(data, dict) else None
    if status != 200 or not isinstance(info, dict):
        raise SystemExit(f"jev: OpenRouter не ответил на проверку ключа (HTTP {status}) — прогон не начат")
    limit, remaining = info.get("limit"), info.get("limit_remaining")
    if not isinstance(limit, (int, float)) or isinstance(limit, bool) or not math.isfinite(limit):
        if not allow_unlimited:
            raise SystemExit("jev: у ключа OpenRouter нет лимита расходов — заведите отдельный ключ с лимитом; прогон не начат")
        if need_usd > UNLIMITED_KEY_BUDGET_CAP:
            raise SystemExit(f"jev: с ключом без лимита потолок прогона не может быть выше {UNLIMITED_KEY_BUDGET_CAP} $ — "
                             "для большого прогона поставьте лимит на ключ; прогон не начат")
        print(f"ВНИМАНИЕ: у ключа OpenRouter нет лимита расходов; прогон разрешён флагом --key-without-limit, "
              f"защита — только потолок клиента {need_usd} $")
        return
    if isinstance(remaining, (int, float)) and not isinstance(remaining, bool) and remaining < need_usd:
        raise SystemExit(f"jev: остаток лимита ключа {remaining} $ меньше потолка прогона {need_usd} $ — прогон не начат")


def request_bound_usd(payload: dict, host: str, price: float) -> float:
    """Верхняя граница цены одной попытки.

    Токен не короче одного байта, поэтому токенов в запросе не больше, чем байт в его теле, плюс постоянная часть
    сервиса; и не больше предела входа — длиннее сервис не примет. По этой границе бронируется бюджет.
    """
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return min(HOSTS[host]["context"], size + OVERHEAD_BOUND_TOKENS) * price / 1e6


# ---------- разбор ответа ----------

def flatten(answer: dict) -> dict:
    """Ответ на вопрос → значение, вероятность выбранного ответа и уверенность на общей шкале."""
    kind = answer.get("type")
    if kind == "noul":
        p = float(answer["noul"])
        return {"value": "да" if p >= 0.5 else "нет", "p_top": round(max(p, 1 - p), 4), "confidence": round(abs(2 * p - 1), 4), "p_yes": p}
    probabilities = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    if kind == "choice":
        value = str(answer["choice"])
        return {"value": value, "p_top": probabilities.get(value, 0.0),      # чужую вероятность выбранному не приписываем
                "confidence": answer.get("confidence")}
    level = max(probabilities, key=probabilities.get) if probabilities else str(round(float(answer["score"])))
    return {"value": level, "p_top": probabilities.get(level, 0.0), "confidence": answer.get("confidence"), "score": answer.get("score")}


def zone(p_top: float, rule: dict | None) -> str:
    if not rule:
        return ""
    if p_top >= float(rule.get("действовать", 1)):
        return "действовать"
    return "проверить" if p_top >= float(rule.get("проверить", 0.5)) else "человеку"


def request_cost(usage: dict, price: float) -> float:
    return float(usage["cost"]) if usage.get("cost") is not None else float(usage.get("input_tokens", 0)) * price / 1e6


# ---------- прогон ----------

def money_settings(args, host: str) -> tuple[str, float]:
    """Версия модели и цена. Модель — только точная версия; цену можно поднять, но не занизить."""
    model = args.model or HOSTS[host]["model"]
    if not MODEL_ID.match(model):
        raise SystemExit(f"jev: модель «{model}» не принята — нужна точная версия вида jev-1.13.0 или typesafe/jev-1.13; "
                         "псевдонимы latest и preview сменятся без предупреждения, и пороги перестанут значить то же")
    price = args.price_per_mtok
    if not math.isfinite(price) or price < PRICE_PER_MTOK_USD:
        raise SystemExit(f"jev: цена {price} $ за млн токенов не принята — она не может быть ниже записанной в клиенте "
                         f"({PRICE_PER_MTOK_USD}); если сервис подешевел, правится константа в коде")
    return model, price


def load_done(path: Path) -> dict:
    """Готовые записи прогона: id → хеш. Оборванная строка отбрасывается, файл переписывается целыми строками."""
    done, good, broken = {}, [], 0
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            done[item["id"]] = item["hash"]
            good.append(line)
        except (ValueError, KeyError, TypeError):
            broken += 1
    if broken:
        path.write_text("".join(line + "\n" for line in good), encoding="utf-8")
        print(f"в {path.name} отброшено оборванных строк: {broken} — эти записи будут отправлены заново")
    return done


def run(args, send=transport) -> int:
    questions, thresholds = load_questions(args.questions)
    if args.shuffle_choices is not None:
        questions = shuffle_choices(questions, args.shuffle_choices)
    records, empty = read_records(args.input, args.text_col, args.id_col, args.limit)
    if not records:
        raise SystemExit("jev: во входном файле нет непустых записей")
    base, host = endpoint()
    model, price = money_settings(args, host)
    fc = forecast(records, questions, price)
    print_forecast(fc, host, model, price)
    check_limits(fc, host)
    if empty:
        print(f"пустых записей пропущено: {empty}")
    for note in question_warnings(questions):
        print("замечание:", note)
    with_contacts = contact_ids(records)
    if with_contacts and not args.contacts_ok:
        raise SystemExit(f"jev: в {len(with_contacts)} записях есть телефон или адрес почты (первые: {', '.join(with_contacts[:5])}). "
                         "Уберите их или добавьте --contacts-ok, если это не контакты людей. Ничего не отправлено.")
    if not args.yes or args.budget_usd is None:
        raise SystemExit("jev: платный прогон стартует только с --yes и --budget-usd <потолок>. Ничего не отправлено.")
    budget = args.budget_usd
    if not math.isfinite(budget) or budget <= 0:
        raise SystemExit("jev: --budget-usd — конечное число больше нуля. Ничего не отправлено.")
    key = os.environ.get(args.key_var)
    if not key:
        raise SystemExit(f"jev: нет переменной {args.key_var}. Положите ключ в окружение или в файл и укажите --env-file. Ничего не отправлено.")
    folder = Path(args.out) / re.sub(r"[^\w.-]+", "_", args.label)
    folder.mkdir(parents=True, exist_ok=True)
    lock_path = folder / "run.lock"
    try:
        os.close(os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise SystemExit(f"jev: прогон «{args.label}» уже идёт или был оборван. Второй запуск отправил бы те же записи повторно. "
                         f"Если другого запуска нет — удалите {lock_path.name} в папке прогона. Ничего не отправлено.")
    try:
        return send_all(args, send, questions, thresholds, records, fc, base, host, model, price, budget, key, folder)
    finally:
        lock_path.unlink(missing_ok=True)


def send_all(args, send, questions, thresholds, records, fc, base, host, model, price, budget, key, folder) -> int:
    results_path = folder / "results.jsonl"
    current = {rid: record_hash(model, state, questions) for rid, state in records}
    done = load_done(results_path)
    foreign = [rid for rid, saved in done.items() if rid in current and current[rid] != saved]
    if foreign:
        raise SystemExit(f"jev: в папке прогона «{args.label}» уже лежат ответы на другие вопросы или от другой версии модели "
                         f"({len(foreign)} записей). Смешивать их нельзя — возьмите новый --label. Ничего не отправлено.")
    todo = [(rid, state) for rid, state in records if rid not in done]
    print(f"к отправке: {len(todo)} из {len(records)}; уже есть в {results_path.name}: {len(records) - len(todo)}")
    worst = fc["варианты"]["с запасом"]["usd"] * len(todo) / len(records)
    if worst > budget:
        raise SystemExit(f"jev: прогноз «с запасом» {worst:.6f} $ больше потолка {budget} $ — "
                         "уменьшите --limit или поднимите потолок. Ничего не отправлено.")
    if host == "openrouter.ai" and todo:
        openrouter_key_guard(send, base, key, budget, args.key_without_limit)

    state = {"spent": 0.0, "unconfirmed": 0.0, "reserved": 0.0, "tokens": 0, "written": 0, "stop": "",
             "models": set(), "latency": []}
    lock, url = threading.Lock(), base + "/v1/systemone"
    out_file = results_path.open("a", encoding="utf-8")

    def work(record):
        rid, record_state = record
        payload = {"model": model, "state": record_state, "questions": questions}
        one = request_bound_usd(payload, host, price)          # верхняя граница цены одной попытки
        hold = one * ATTEMPTS                                  # под запрос в полёте бронируются все его попытки
        with lock:
            if state["stop"]:
                return
            if state["spent"] + state["unconfirmed"] + state["reserved"] + hold > budget:
                state["stop"] = f"потолок бюджета {budget} $: на следующую запись с её повторами не хватает"
                return
            state["reserved"] += hold
        started = time.time()
        try:
            data, unknown = call(send, url, key, payload)
        except Stop as error:
            with lock:
                state["reserved"] -= hold
                state["unconfirmed"] += error.unconfirmed * one
                state["stop"] = state["stop"] or str(error)
            return
        except Exception as error:                  # сбой самого клиента тоже останавливает прогон, а не теряется в потоке
            with lock:
                state["reserved"] -= hold
                state["unconfirmed"] += one
                state["stop"] = state["stop"] or f"сбой клиента: {type(error).__name__}"
            return
        usage = data["usage"]
        line = {"id": rid, "hash": current[rid], "model": data.get("model"),
                "latency_ms": round((time.time() - started) * 1000), "usage": usage, "answers": data["answers"]}
        with lock:
            cost = request_cost(usage, price)
            state["reserved"] -= hold
            state["spent"] += cost
            state["unconfirmed"] += unknown * one
            state["tokens"] += usage["input_tokens"]
            state["models"].add(str(data.get("model")))
            state["latency"].append(line["latency_ms"])
            out_file.write(json.dumps(line, ensure_ascii=False) + "\n")
            out_file.flush()
            state["written"] += 1
            if cost > one:                          # граница оказалась неверной — дальше бронь ничего не гарантирует
                over = state["spent"] + state["unconfirmed"] - budget
                state["stop"] = state["stop"] or (
                    f"запрос стоил {cost:.6f} $ — больше расчётной верхней границы {one:.6f} $: сервис берёт дороже "
                    "документированной цены; прогон остановлен, чтобы не выйти за потолок бюджета"
                    + (f" (потолок уже превышен на {over:.6f} $)" if over > 0 else ""))

    try:
        if todo:
            work(todo[0])                           # первый запрос идёт один: по нему видна настоящая цена записи
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
            list(pool.map(work, todo[1:]))
    finally:
        out_file.close()

    rows = write_csv(results_path, folder / "results.csv", thresholds, current)
    latency = sorted(state["latency"])
    summary = {"at": datetime.now().isoformat(timespec="seconds"), "label": args.label, "host": host, "model_asked": model,
               "model_answered": sorted(state["models"]), "records": len(records), "sent_now": state["written"],
               "rows_in_csv": rows, "input_tokens": state["tokens"], "usd": round(state["spent"], 6),
               "usd_unconfirmed": round(state["unconfirmed"], 6),
               "tokens_per_record": round(state["tokens"] / state["written"]) if state["written"] else None,
               "latency_ms_median": latency[len(latency) // 2] if latency else None, "stopped": state["stop"],
               "shuffle_choices": args.shuffle_choices, "questions_file": str(args.questions), "input": str(args.input)}
    (folder / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(Path(args.out) / "manifest.jsonl", "a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(f"отправлено сейчас: {state['written']}; строк в results.csv: {rows}; входных токенов: {state['tokens']}; "
          f"потрачено: {state['spent']:.6f} $; версия модели: {', '.join(sorted(state['models'])) or '—'}")
    if state["unconfirmed"]:
        print(f"возможно списано сверх этого: до {state['unconfirmed']:.6f} $ — попытки без принятого ответа (таймаут, сбой "
              "сервера, ответ не прошёл проверку); сверьте с кабинетом сервиса")
    if state["written"]:
        print(f"токенов на запись в среднем: {summary['tokens_per_record']}; время ответа, медиана: {summary['latency_ms_median']} мс")
    if state["stop"]:
        print("ПРОГОН ОСТАНОВЛЕН:", state["stop"])
        return 3
    return 0 if rows else 2


def read_results(path) -> dict:
    """Результаты прогона: id → запись. Оборванная строка пропускается; при повторе побеждает последняя строка."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            out[item["id"]] = item
        except (ValueError, KeyError, TypeError):
            continue
    return out


def write_csv(results_path, csv_path, thresholds: dict, current: dict | None = None) -> int:
    """Плоская таблица ответов. current — id → хеш записей этого прогона: строки других вопросов или модели не попадают."""
    results = read_results(results_path) if Path(results_path).exists() else {}
    if current is not None:
        results = {rid: item for rid, item in results.items() if current.get(rid) == item.get("hash")}
    names = sorted({name for item in results.values() for name in item["answers"]})
    fields = ["id", "model"] + [f"{n}.{part}" for n in names for part in ("значение", "вероятность", "уверенность", "зона")]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for rid, item in results.items():
            row = {"id": rid, "model": item.get("model")}
            for name, answer in item["answers"].items():
                flat = flatten(answer)
                row.update({f"{name}.значение": flat["value"], f"{name}.вероятность": flat["p_top"],
                            f"{name}.уверенность": flat["confidence"], f"{name}.зона": zone(flat["p_top"], thresholds.get(name))})
            writer.writerow(row)
    return len(results)


# ---------- сверка с эталоном ----------

def wilson(hits: int, n: int) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    z, p = 1.96, hits / n
    centre, spread = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n), (centre + spread) / (1 + z * z / n)


def normal(value, kind: str) -> str:
    """«Да/нет» в эталоне пишут по-разному; варианты выбора и уровни шкалы сравниваются как есть."""
    value = str(value).strip()
    if kind != "noul":
        return value
    return {"1": "да", "true": "да", "yes": "да", "0": "нет", "false": "нет", "no": "нет"}.get(value.lower(), value.lower())


def evaluate(results: dict, gold_rows: list[dict], id_col: str) -> dict:
    """Совпадение с человеческой разметкой по каждому вопросу: всего, по классам, по корзинам вероятности, по порогам."""
    report = {}
    names = [c for c in (gold_rows[0].keys() if gold_rows else []) if c != id_col]
    for name in names:
        pairs = []
        for row in gold_rows:
            item = results.get(str(row[id_col]).strip())
            if item and name in item["answers"] and str(row.get(name) or "").strip() != "":
                kind, flat = item["answers"][name].get("type"), flatten(item["answers"][name])
                pairs.append((normal(row[name], kind), normal(flat["value"], kind), flat["p_top"]))
        if not pairs:
            continue
        hits = sum(g == v for g, v, _ in pairs)
        low, high = wilson(hits, len(pairs))
        classes = {}
        for label in sorted({g for g, _, _ in pairs}):
            own = [(g, v) for g, v, _ in pairs if g == label]
            said = [(g, v) for g, v, _ in pairs if v == label]
            classes[label] = {"в эталоне": len(own), "найдено из них": sum(g == v for g, v in own),
                              "названо моделью": len(said), "из них верно": sum(g == v for g, v in said)}
        bins = {}
        for lo, hi in BINS:
            inside = [(g, v) for g, v, p in pairs if lo <= p < hi]
            bins[f"{lo:.1f}–{min(hi, 1.0):.1f}"] = {"ответов": len(inside), "верных": sum(g == v for g, v in inside)}
        thresholds = {}
        for t in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
            auto = [(g, v) for g, v, p in pairs if p >= t]
            thresholds[str(t)] = {"решено без человека": len(auto), "доля": round(len(auto) / len(pairs), 3),
                                  "ошибок среди них": sum(g != v for g, v in auto)}
        report[name] = {"сверено": len(pairs), "совпало": hits, "доля": round(hits / len(pairs), 3),
                        "интервал_95": [round(low, 3), round(high, 3)], "классы": classes, "корзины": bins, "пороги": thresholds}
    return report


def compare(first: dict, second: dict) -> dict:
    """Два прогона одних записей: сколько ответов изменилось (повторяемость, перестановка вариантов)."""
    out = {}
    for rid in first.keys() & second.keys():
        for name in first[rid]["answers"].keys() & second[rid]["answers"].keys():
            a, b = flatten(first[rid]["answers"][name]), flatten(second[rid]["answers"][name])
            item = out.setdefault(name, {"сравнено": 0, "ответ изменился": 0, "сдвиг вероятности": 0.0})
            item["сравнено"] += 1
            item["ответ изменился"] += a["value"] != b["value"]
            item["сдвиг вероятности"] += abs(a["p_top"] - b["p_top"])
    for item in out.values():
        item["сдвиг вероятности"] = round(item["сдвиг вероятности"] / item["сравнено"], 4)
    return out


def print_eval(report: dict) -> None:
    for name, item in report.items():
        low, high = item["интервал_95"]
        print(f"\n[{name}] совпало {item['совпало']} из {item['сверено']} = {item['доля']:.1%} (95 % интервал {low:.1%}–{high:.1%})")
        for label, c in item["классы"].items():
            print(f"  класс «{label}»: в эталоне {c['в эталоне']}, найдено {c['найдено из них']}; "
                  f"модель назвала {c['названо моделью']}, из них верно {c['из них верно']}")
        print("  честность вероятностей: " + "; ".join(f"{k}: {v['верных']} из {v['ответов']}" for k, v in item["корзины"].items()))
        print("  порог → решено без человека (доля) → ошибок: " + "; ".join(
            f"{t}: {v['решено без человека']} ({v['доля']:.0%}) → {v['ошибок среди них']}" for t, v in item["пороги"].items()))


# ---------- проверка связи ----------

PING_STATE = "Мастер опоздал на два часа и не извинился, но прибор установил аккуратно."
PING_QUESTIONS = {
    "недоволен": {"type": "noul", "instructions": "Есть ли у автора отзыва претензия к работе компании?",
                  "criteria": {"true": "названа хотя бы одна претензия", "false": "претензий нет"}},
    "тема": {"type": "choice", "instructions": "На что автор жалуется в первую очередь?",
             "criteria": {"сроки": "опоздание или срыв срока", "качество": "плохо выполненная работа",
                          "нет_претензий": "претензий нет"}},
    "сила": {"type": "score", "instructions": "Насколько сильно автор раздражён?",
             "criteria": ["спокоен, претензий нет", "сдержанно отмечает минус", "явно зол, требует или угрожает"]},
}


def ping(args, send=transport) -> int:
    """Проверка связи: ровно один фиксированный запрос без повторов — работает ли ключ и отвечает ли модель.

    Запись и вопросы зашиты в код, поэтому цена ограничена одним коротким запросом. Ключ OpenRouter без лимита
    здесь допускается с предупреждением: израсходовать счёт одним запросом нельзя; команда run такой ключ не примет.
    """
    base, host = endpoint()
    model, price = money_settings(args, host)
    payload = {"model": model, "state": PING_STATE, "questions": PING_QUESTIONS}
    bound = request_bound_usd(payload, host, price)
    print(f"проверка связи: один запрос на {base}/v1/systemone, модель {model}; "
          f"цена не выше {bound:.6f} $ при документированной цене")
    if not args.yes:
        print("ничего не отправлено: добавьте --yes, чтобы отправить этот один запрос")
        return 0
    key = os.environ.get(args.key_var)
    if not key:
        raise SystemExit(f"jev: нет переменной {args.key_var}. Положите ключ в окружение или в файл и укажите --env-file. "
                         "Ничего не отправлено.")
    if host == "openrouter.ai":
        status, data, _ = send("GET", base + "/v1/key", key)
        info = data.get("data") if status == 200 and isinstance(data, dict) else None
        if not isinstance(info, dict):
            raise SystemExit(f"jev: OpenRouter не принял ключ (HTTP {status}) — запрос к модели не отправлен")
        print(f"ключ OpenRouter: лимит расходов {info.get('limit')}, остаток лимита {info.get('limit_remaining')}, "
              f"израсходовано {info.get('usage')} $, бесплатный уровень: {'да' if info.get('is_free_tier') else 'нет'}")
        if info.get("limit") is None:
            print("ВНИМАНИЕ: у ключа нет лимита расходов — команда run с ним работать не будет; для прогонов нужен ключ с лимитом")
    started = time.time()
    try:
        data, _ = call(send, base + "/v1/systemone", key, payload, attempts=1)
    except Stop as error:
        print("СЕРВИС НЕ ОТВЕТИЛ КАК ОЖИДАЛОСЬ:", error)
        if error.unconfirmed:
            print(f"возможно списано: до {bound:.6f} $ — сверьте с кабинетом сервиса")
        return 3
    usage = data["usage"]
    cost = request_cost(usage, price)
    print(f"ответила модель {data.get('model')}; время ответа {round((time.time() - started) * 1000)} мс; "
          f"входных токенов {usage['input_tokens']}; стоимость {cost:.8f} $ "
          + ("(по данным сервиса)" if usage.get("cost") is not None else "(по прайсу)"))
    print(f"запись: «{PING_STATE}»")
    for name, answer in data["answers"].items():
        if name in PING_QUESTIONS:
            flat = flatten(answer)
            print(f"  {name}: {flat['value']} — вероятность {flat['p_top']}, уверенность {flat['confidence']}")
    if cost > bound:
        print(f"ВНИМАНИЕ: запрос стоил больше расчётной границы {bound:.6f} $ — сервис берёт дороже документированной цены")
    return 0


# ---------- команды ----------

def main(argv=None, send=transport) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=["check", "ping", "plan", "run", "eval"])
    parser.add_argument("--questions", help="файл вопросов (JSON)")
    parser.add_argument("--input", help="записи: CSV или JSONL")
    parser.add_argument("--text-col", action="append", default=[], help="колонка с текстом; можно несколько")
    parser.add_argument("--id-col", help="колонка с идентификатором записи")
    parser.add_argument("--limit", type=int, help="взять первые N записей")
    parser.add_argument("--out", help="папка результатов")
    parser.add_argument("--label", default="прогон", help="имя прогона; повтор с тем же именем продолжает его")
    parser.add_argument("--budget-usd", type=float, help="потолок расходов прогона, $")
    parser.add_argument("--yes", action="store_true", help="подтверждение платного прогона")
    parser.add_argument("--workers", type=int, default=4, help="одновременных запросов, не больше 8")
    parser.add_argument("--model", help="версия модели; по умолчанию закреплённая для адреса")
    parser.add_argument("--price-per-mtok", type=float, default=PRICE_PER_MTOK_USD, help="цена входа, $ за млн токенов")
    parser.add_argument("--shuffle-choices", type=int, help="переставить варианты выбора (число — зерно перестановки)")
    parser.add_argument("--contacts-ok", action="store_true", help="телефоны и почта в записях — не контакты людей")
    parser.add_argument("--env-file", help="файл с переменной ключа и TYPESAFE_BASE_URL")
    parser.add_argument("--key-without-limit", action="store_true",
                        help="владелец разрешил ключ OpenRouter без лимита; только при потолке не выше 0,5 $")
    parser.add_argument("--key-var", default=KEY_VAR, help="имя переменной окружения с ключом; по умолчанию TYPESAFE_API_KEY")
    parser.add_argument("--show", action="store_true", help="plan: напечатать первый запрос целиком")
    parser.add_argument("--results", help="eval: results.jsonl прогона")
    parser.add_argument("--gold", help="eval: эталон — CSV с колонкой id и колонкой на каждый вопрос")
    parser.add_argument("--compare", help="eval: results.jsonl второго прогона тех же записей")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.key_var):
        raise SystemExit("jev: --key-var — имя переменной окружения, а не сам ключ: заглавные латинские буквы, цифры и «_»")
    if args.env_file:
        load_env_file(args.env_file, args.key_var)
    if args.verb == "ping":
        return ping(args, send)

    if args.verb == "eval":
        if not args.results or not (args.gold or args.compare):
            raise SystemExit("eval: нужны --results и --gold или --compare")
        results = read_results(args.results)
        if args.gold:
            text = Path(args.gold).read_text(encoding="utf-8-sig")
            gold_rows = list(csv.DictReader(io.StringIO(text, newline=""), delimiter=max(";,\t", key=text.split("\n", 1)[0].count)))
            id_col = args.id_col or "id"
            if not gold_rows or id_col not in gold_rows[0]:
                raise SystemExit(f"eval: в эталоне нет колонки «{id_col}»")
            report = evaluate(results, gold_rows, id_col)
            if not report:
                raise SystemExit("eval: ни одна запись эталона не совпала с результатами по идентификатору и имени вопроса")
            print_eval(report)
        if args.compare:
            for name, item in compare(results, read_results(args.compare)).items():
                print(f"[{name}] сравнено {item['сравнено']}, ответ изменился в {item['ответ изменился']}, "
                      f"средний сдвиг вероятности {item['сдвиг вероятности']}")
        return 0

    if not args.questions:
        raise SystemExit(f"{args.verb}: нужен --questions")
    questions, thresholds = load_questions(args.questions)
    if args.verb == "check":
        for name, q in questions.items():
            size = len(q["criteria"]) if q.get("criteria") else 0
            print(f"{name}: {q['type']}, вариантов или уровней: {size}, порог: {thresholds.get(name) or 'не задан'}")
        for note in question_warnings(questions):
            print("замечание:", note)
        print(f"вопросов: {len(questions)} — файл принят")
        return 0

    if not args.input or not args.text_col and not str(args.input).lower().endswith(".jsonl"):
        raise SystemExit(f"{args.verb}: нужны --input и --text-col (для JSONL колонка не нужна)")
    if args.verb == "run":
        if not args.out:
            raise SystemExit("run: нужен --out")
        return run(args, send)

    records, empty = read_records(args.input, args.text_col, args.id_col, args.limit)
    if not records:
        raise SystemExit("jev: во входном файле нет непустых записей")
    base, host = endpoint()
    model, price = money_settings(args, host)
    if args.shuffle_choices is not None:
        questions = shuffle_choices(questions, args.shuffle_choices)
    fc = forecast(records, questions, price)
    print("ПРОБНЫЙ РЕЖИМ: ничего не отправлено, денег не потрачено")
    print_forecast(fc, host, model, price)
    check_limits(fc, host)
    if empty:
        print(f"пустых записей пропущено: {empty}")
    for note in question_warnings(questions):
        print("замечание:", note)
    with_contacts = contact_ids(records)
    if with_contacts:
        print(f"ВНИМАНИЕ: в {len(with_contacts)} записях телефон или адрес почты (первые: {', '.join(with_contacts[:5])}) — "
              "прогон их не отправит без --contacts-ok")
    body = {"model": model, "state": records[0][1], "questions": questions}
    if args.show:
        print(json.dumps(body, ensure_ascii=False, indent=1))
    else:
        print(f"первый запрос: запись {records[0][0]}, {len(as_text(records[0][1]))} симв.; вопросы: {', '.join(questions)} "
              "(целиком — с --show)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
