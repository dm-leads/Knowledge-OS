# -*- coding: utf-8 -*-
"""Клиент Keys.so API: любой метод, ожидание отчётов, все страницы, снимок на диск.

Каждый вызов сохраняет сырой JSON и CSV строк в <out>/<дата>/ и пишет строку в <out>/manifest.jsonl:
что сняли, с какими параметрами, сколько строк. Подписка кончается — снимки остаются.

Запуск (любой Python с python-dotenv):
    python keysso.py limits
    python keysso.py get /report/simple/domain_dashboard base=msk domain=бризекс.рф --out <папка>
    python keysso.py get /report/simple/organic/keywords base=msk domain=tion.ru \
        "sort=wsk|desc" "filter=pos<=10^wsk>=10" --all --max-rows 20000 --out <папка>
    python keysso.py post /tools/check-top base=msk --json тело.json --out <папка>

Правила, которые клиент соблюдает сам:
- кириллический домен передаётся как есть (бризекс.рф); punycode API не находит;
- ответ с полем code=202 — отчёт ещё строится: ждём и переспрашиваем, до 6 минут;
- не больше 10 запросов за 10 секунд (лимит API);
- при --all листаем страницы; если сортировка по одному полю — добавляем второе, иначе строки на стыке
  страниц теряются или дублируются;
- 0 строк при --all — код выхода 2: пустой результат не выдаётся за успех.
Ключ — KEYSO_API_KEY в мастер-.env, значение не печатается.
"""
import argparse, csv, io, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

ENV = dotenv_values(Path(r"C:\Users\redmi\Second Brain Secrets\.env"))
API = "https://api.keys.so"
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
_calls: list = []


def _throttle():
    now = time.time()
    while _calls and now - _calls[0] > 10:
        _calls.pop(0)
    if len(_calls) >= 9:
        time.sleep(10 - (now - _calls[0]) + 0.2)
    _calls.append(time.time())


def call(method: str, path: str, params: dict, body=None) -> dict:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    waited = 0
    for attempt in range(8):
        _throttle()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "X-Keyso-TOKEN": ENV["KEYSO_API_KEY"], "Content-Type": "application/json", "Accept": "application/json"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")[:400]
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After") or 10)); continue
            if e.code in (500, 502, 503, 504) and attempt < 4:
                time.sleep(3 * (attempt + 1)); continue
            hint = {401: "ключ не принят", 402: "ограничение тарифа на этот запрос",
                    404: "не найдено — домен передавай кириллицей, не punycode"}.get(e.code, "")
            raise SystemExit(f"Keys.so {method} {path}: HTTP {e.code} {hint} | {txt}")
        if isinstance(resp, dict) and resp.get("code") == 202 and waited < 360:
            time.sleep(15); waited += 15; continue  # отчёт строится
        return resp
    raise SystemExit(f"Keys.so {method} {path}: не дождались ответа")


def rows_of(resp) -> list:
    if isinstance(resp, dict) and isinstance(resp.get("data"), list):
        return resp["data"]
    if isinstance(resp, list):
        return resp
    return []


def save(out: str, label: str, params: dict, resp, rows: list) -> str:
    day = datetime.now().strftime("%Y-%m-%d")
    folder = Path(out) / day
    folder.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^\w.-]+", "_", label + "_" + "_".join(f"{k}-{v}" for k, v in params.items()
                                                          if k not in ("page", "per_page")))[:150]
    stem = folder / f"{datetime.now():%H%M%S}_{tag}"
    Path(str(stem) + ".json").write_text(json.dumps(resp, ensure_ascii=False, indent=1), encoding="utf-8")
    if rows and isinstance(rows[0], dict):
        keys = list({k: None for r in rows for k in r})
        with open(str(stem) + ".csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                            for k, v in r.items()})
    with open(Path(out) / "manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "call": label,
                            "params": params, "rows": len(rows), "file": str(stem)}, ensure_ascii=False) + "\n")
    return str(stem)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("verb", choices=["limits", "get", "post"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("params", nargs="*", help="ключ=значение")
    ap.add_argument("--json", help="файл с телом запроса (post)")
    ap.add_argument("--all", action="store_true", help="выгрузить все страницы")
    ap.add_argument("--per-page", type=int, default=1000)
    ap.add_argument("--max-rows", type=int, default=50000)
    ap.add_argument("--tie", help="второе поле сортировки для постраничной выгрузки, напр. word|asc или id|asc")
    ap.add_argument("--out", default="keysso_out")
    a = ap.parse_args()

    if a.verb == "limits":
        resp = call("GET", "/limits/all", {})
        print(json.dumps(resp, ensure_ascii=False, indent=1))
        save(a.out, "limits", {}, resp, [])
        return 0

    # Git Bash превращает "/report/..." в "C:/Program Files/Git/report/..." — возвращаем путь API
    a.path = re.sub(r"^[A-Za-z]:/.*?/Git(?=/)", "", a.path.replace("\\", "/"))
    if not a.path.startswith("/"):
        a.path = "/" + a.path
    params = dict(p.split("=", 1) for p in a.params)
    body = json.load(io.open(a.json, encoding="utf-8-sig")) if a.json else None
    label = a.path.strip("/").replace("/", ".")

    if a.verb == "post":
        resp = call("POST", a.path, params, body)
        rows = rows_of(resp)
        print(f"Ответ: строк {len(rows)} → {save(a.out, label, params, resp, rows)}")
        return 0

    if not a.all:
        resp = call("GET", a.path, params)
        rows = rows_of(resp)
        print(f"Строк: {len(rows)} (всего по отчёту: {resp.get('total') if isinstance(resp, dict) else '—'}) → "
              f"{save(a.out, label, params, resp, rows)}")
        return 0

    sort = params.get("sort", "")
    tie = a.tie or ("word|asc" if not sort.startswith("word") else "ws|desc")
    if sort and "," not in sort:
        params["sort"] = f"{sort},{tie}"
    params["per_page"] = str(a.per_page)
    rows, page, last = [], 1, 1
    while page <= last and len(rows) < a.max_rows:
        params["page"] = str(page)
        try:
            resp = call("GET", a.path, params)
        except SystemExit as e:
            # отчёт не знает поле второй сортировки — повторяем с одной, предупредив о риске стыков
            if page == 1 and "," in params.get("sort", "") and "не найдено или недоступно" in str(e):
                params["sort"] = sort
                print(f"  ! поле {tie.split('|')[0]} в отчёте нет — сортирую только по {sort}; "
                      f"передай --tie с полем этого отчёта, чтобы стыки страниц были надёжны")
                resp = call("GET", a.path, params)
            else:
                raise
        chunk = rows_of(resp)
        rows += chunk
        last = int(resp.get("last_page") or 1) if isinstance(resp, dict) else 1
        print(f"  страница {page}/{last}: +{len(chunk)}")
        if not chunk:
            break
        page += 1
    rows = rows[: a.max_rows]
    params.pop("page", None)
    stem = save(a.out, label, params, {"total_rows": len(rows), "data": rows}, rows)
    print(f"Выгружено строк: {len(rows)} → {stem}")
    if not rows:
        print("Пустой результат — проверь домен, базу и фильтр.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
