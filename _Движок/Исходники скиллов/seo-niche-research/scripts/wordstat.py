# -*- coding: utf-8 -*-
"""Wordstat через Yandex Cloud Search API v2: спрос по фразам и помесячная динамика.

Старый API api.wordstat.yandex.net мёртв (ошибка сертификата с августа 2026); этот работает
на ключе Search API. Запросы тарифицируются Яндексом — на большие пачки сначала назови цену.

Ключи из мастер-.env (значения не печатаются): YANDEX_SEARCH_API_KEY, YANDEX_FOLDER_ID.

Запуск (любой Python с python-dotenv):
    python wordstat.py top бризер "приточная вентиляция" --n 50 --out <папка>
    python wordstat.py dynamics бризер --months 24 --out <папка>
    python wordstat.py top --file фразы.txt --out <папка>        # по фразе на строку

Регион по умолчанию 225 (Россия), --region 213 — Москва.
Выход: CSV в <папка>. Если ни по одной фразе данных нет — код выхода 2.
"""
import argparse, csv, json, os, sys, time, urllib.error, urllib.request
from datetime import date, timedelta
from pathlib import Path

from dotenv import dotenv_values

ENV = dotenv_values(Path(r"C:\Users\redmi\Second Brain Secrets\.env"))
URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/"
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def call(method: str, body: dict) -> dict:
    body["folderId"] = ENV["YANDEX_FOLDER_ID"]
    req = urllib.request.Request(URL + method, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Api-Key " + ENV["YANDEX_SEARCH_API_KEY"],
                                          "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 3)  # временная ошибка — пауза и повтор
                continue
            raise SystemExit(f"Wordstat {method}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["top", "dynamics"])
    ap.add_argument("phrases", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--n", type=int, default=50, help="сколько фраз-расширений вернуть (top)")
    ap.add_argument("--months", type=int, default=24, help="глубина динамики, месяцев")
    ap.add_argument("--region", default="225")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()

    phrases = list(a.phrases)
    if a.file:
        phrases += [l.strip() for l in open(a.file, encoding="utf-8-sig") if l.strip()]
    if not phrases:
        ap.error("нет фраз")

    rows = []
    # API ждёт конец периода = последний день месяца: берём последний полный месяц
    end = date.today().replace(day=1) - timedelta(days=1)
    y, m = end.year, end.month - (a.months - 1)
    while m <= 0:
        y, m = y - 1, m + 12
    start = date(y, m, 1)
    for ph in phrases:
        if a.mode == "top":
            d = call("topRequests", {"phrase": ph, "numPhrases": str(a.n), "regions": [a.region]})
            for kind in ("results", "associations"):
                for r in d.get(kind, []):
                    rows.append({"seed": ph, "type": "расширение" if kind == "results" else "ассоциация",
                                 "phrase": r["phrase"], "count": int(r["count"])})
        else:
            d = call("dynamics", {"phrase": ph, "period": "PERIOD_MONTHLY", "regions": [a.region],
                                  "fromDate": f"{start:%Y-%m-%d}T00:00:00Z",
                                  "toDate": f"{end:%Y-%m-%d}T00:00:00Z"})
            for r in d.get("results", []):
                rows.append({"seed": ph, "month": r["date"][:7], "count": int(r["count"]), "share": r.get("share")})
        print(f"{ph}: {sum(1 for r in rows if r['seed'] == ph)} строк")

    if not rows:
        print("Wordstat не вернул данных ни по одной фразе.", file=sys.stderr)
        return 2
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"wordstat_{a.mode}_{date.today():%Y-%m-%d}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"Записано строк: {len(rows)} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
