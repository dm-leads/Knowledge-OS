# -*- coding: utf-8 -*-
"""Клиент Topvisor API — только чтение: проект сайта и история позиций по запросам.

Topvisor хранит позиции сайта в поиске по региону за все даты, когда запускалась проверка. Чтение истории
бесплатно; запуск проверки платный — в клиенте его нет намеренно: проверку и расписание запускает человек в
интерфейсе, где видна цена. Клиент ходит только в методы чтения (`get/…`) и только в проект из мастер-.env:
в аккаунте могут лежать чужие проекты.

Каждый вызов сохраняет сырой JSON и CSV строк в <out>/<дата>/ и пишет строку в <out>/manifest.jsonl:
что сняли, с какими параметрами, сколько строк.

Запуск (любой Python с python-dotenv):
    python topvisor.py project
    python topvisor.py history --queries запросы.csv [--queries ещё.csv] --out <папка> [--from 2025-07-01] [--to 2026-10-04]
        запросы.csv — колонка «запрос», разделитель «;»

Правила, которые клиент соблюдает сам:
- позиция в ответе — строка: число — место в выдаче, «--» — проверка была, сайт в глубине проверки не найден;
  даты, когда запрос не проверялся, в ответе нет вовсе. Пустое и «не найден» — разные состояния, в ноль не сводятся;
- 0 строк — код выхода 2: пустой результат не выдаётся за успех.
Ключи — Topvisor_API_KEY, Topvisor_User_Id, Topvisor_ID_Project в мастер-.env, значения не печатаются.
"""
import argparse, csv, json, re, sys, urllib.error, urllib.request
from datetime import date, datetime
from pathlib import Path

from dotenv import dotenv_values

ENV = {k.lower(): v for k, v in dotenv_values(Path(r"C:\Users\redmi\Second Brain Secrets\.env")).items()}
API = "https://api.topvisor.com/v2/json/"
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
NOT_FOUND = "--"


def call(path: str, payload: dict) -> dict:
    """Запрос к методу чтения. Запись и платные действия отсекаются здесь, а не обещанием в описании."""
    if not path.startswith("get/"):
        raise SystemExit(f"Topvisor {path}: клиент только читает — методы вне get/ не вызываются")
    request = urllib.request.Request(API + path, data=json.dumps(payload).encode("utf-8"), headers={
        "Content-Type": "application/json", "User-Id": ENV["topvisor_user_id"],
        "Authorization": f"bearer {ENV['topvisor_api_key']}"})
    try:
        answer = json.load(urllib.request.urlopen(request, timeout=120))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Topvisor {path}: HTTP {e.code} | {e.read().decode('utf-8', 'replace')[:300]}")
    if answer.get("errors"):
        raise SystemExit(f"Topvisor {path}: " + "; ".join(f"{e.get('code')} {str(e.get('string'))[:200]}"
                                                          for e in answer["errors"]))
    return answer


def project_id() -> int:
    return int(ENV["topvisor_id_project"])


def save(out: str, label: str, params: dict, resp, rows: list) -> str:
    folder = Path(out) / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^\w.-]+", "_", label + "_" + "_".join(f"{k}-{v}" for k, v in params.items()))[:150]
    stem = folder / f"{datetime.now():%H%M%S}_{tag}"
    Path(str(stem) + ".json").write_text(json.dumps(resp, ensure_ascii=False, indent=1), encoding="utf-8")
    if rows:
        with open(str(stem) + ".csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    with open(Path(out) / "manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "call": label, "params": params,
                            "rows": len(rows), "file": str(stem)}, ensure_ascii=False) + "\n")
    return str(stem)


def project() -> dict:
    """Проект сайта: название, сайт, поисковики и регионы с их номерами (номер региона нужен истории)."""
    answer = call("get/projects_2/projects", {"fields": ["id", "name", "site", "date"], "show_searchers_and_regions": 1,
                                              "filters": [{"name": "id", "operator": "EQUALS", "values": [project_id()]}]})
    found = [p for p in answer.get("result") or [] if str(p.get("id")) == str(project_id())]
    if not found:
        raise SystemExit("Topvisor: проект из Topvisor_ID_Project в аккаунте не найден")
    return found[0]


def read_queries(paths) -> list[str]:
    names = []
    for path in paths:
        with open(path, encoding="utf-8-sig", newline="") as f:
            names += [row["запрос"].strip() for row in csv.DictReader(f, delimiter=";") if (row.get("запрос") or "").strip()]
    if not names:
        raise SystemExit("Topvisor: в файлах запросов нет колонки «запрос» или она пуста")
    return list(dict.fromkeys(names))


def flatten(keywords, searcher: str, region: str, region_index: int) -> list[dict]:
    """Ответ истории → строка на запрос и дату съёма. Ключ ячейки — «дата:проект:номер региона»."""
    rows = []
    for item in keywords:
        for key, cell in sorted((item.get("positionsData") or {}).items()):
            day, _, index = key.split(":")
            if int(index) != region_index:
                continue
            raw = str((cell or {}).get("position") or "")
            if raw == "":
                continue                                # ячейка без значения — проверки не было
            rows.append({"date": day, "searcher": searcher, "region": region, "region_index": region_index,
                         "word": item.get("name"), "group": item.get("group_name"), "position_raw": raw,
                         "position": raw if raw.isdigit() else "", "found": 1 if raw.isdigit() else 0})
    return rows


def history(names, date_from: str, date_to: str, region_index: int) -> tuple[dict, list[dict], list[str]]:
    info = project()
    pair = next(((s.get("name"), r.get("name")) for s in info.get("searchers") or [] for r in s.get("regions") or []
                 if int(r.get("index")) == region_index), None)
    if pair is None:
        raise SystemExit(f"Topvisor: региона с номером {region_index} в проекте нет")
    by_name = [{"name": "name", "operator": "IN", "values": names}]
    base = {"project_id": project_id(), "regions_indexes": [region_index], "fields": ["name", "group_name"],
            "positions_fields": ["position"], "filters": by_name, "limit": 10000}
    first = call("get/positions_2/history", {**base, "date1": date_from, "date2": date_to, "show_exists_dates": 1})
    dates = [d for d in (first.get("result") or {}).get("existsDates") or [] if date_from <= d <= date_to]
    if not dates:
        raise SystemExit(f"Topvisor: за {date_from}–{date_to} в регионе {pair[1]} съёмов нет")
    keywords = {}
    for i in range(0, len(dates), 30):                       # даты порциями — ответ остаётся обозримым
        answer = call("get/positions_2/history", {**base, "dates": dates[i:i + 30]})
        for item in (answer.get("result") or {}).get("keywords") or []:
            merged = keywords.setdefault(item.get("name"), {"name": item.get("name"), "group_name": item.get("group_name"),
                                                            "positionsData": {}})
            merged["positionsData"].update(item.get("positionsData") or {})
    rows = flatten(keywords.values(), pair[0], pair[1], region_index)
    return {"project": info.get("name"), "dates": dates, "keywords": list(keywords.values())}, rows, dates


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=["project", "history"])
    parser.add_argument("--queries", action="append", help="csv с колонкой «запрос» (разделитель «;»); можно несколько")
    parser.add_argument("--from", dest="date_from", default="2020-01-01", help="первая дата ГГГГ-ММ-ДД")
    parser.add_argument("--to", dest="date_to", default=date.today().isoformat(), help="последняя дата ГГГГ-ММ-ДД")
    parser.add_argument("--region-index", type=int, default=1, help="номер региона в проекте (см. project)")
    parser.add_argument("--out", help="папка архива снимков")
    args = parser.parse_args(argv)
    if args.verb == "project":
        info = project()
        print(f"проект: {info.get('name')} | сайт: {info.get('site')} | создан: {info.get('date')}")
        for searcher in info.get("searchers") or []:
            for region in searcher.get("regions") or []:
                print(f"  {searcher.get('name')} · {region.get('name')} · номер региона {region.get('index')}")
        return 0
    if not args.queries or not args.out:
        raise SystemExit("history: нужны --queries и --out")
    names = read_queries(args.queries)
    raw, rows, dates = history(names, args.date_from, args.date_to, args.region_index)
    stem = save(args.out, f"history.{project_id()}", {"from": dates[0], "to": dates[-1], "region": args.region_index},
                raw, rows)
    with_rows = {r["word"] for r in rows}
    print(f"запросов запрошено {len(names)}, с рядом {len(with_rows)}; дат съёмов {len(dates)} "
          f"({dates[0]} … {dates[-1]}); строк {len(rows)} → {stem}")
    missing = [n for n in names if n not in with_rows]
    if missing:
        print("нет в проекте или без съёмов: " + "; ".join(missing))
    return 0 if rows else 2


if __name__ == "__main__":
    sys.exit(main())
