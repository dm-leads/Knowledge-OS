# -*- coding: utf-8 -*-
"""Инвентаризация контента конкурентов по их картам сайтов (advertools).

Что даёт: какие разделы есть у сайта, сколько в них страниц, что опубликовано или обновлено
за последние N дней — куда конкурент сейчас вкладывается. Работает для любого поисковика:
данные берутся с самих сайтов, платных баз не нужно.

Чего не даёт: по каким запросам страница ранжируется и сколько трафика получает — это
только в коммерческих базах выдачи.

Запуск (окружение с advertools):
    C:/Users/redmi/Tools/seo-venv/Scripts/python.exe competitor_inventory.py tion.ru ballu.ru \
        --out <папка> [--recent 90] [--section blog]

Кириллические домены (бризекс.рф) переводятся в punycode автоматически.
Выход: <папка>/inventory_<дата>.csv (все URL) и сводка в консоль. Если ни у одного домена
не нашлось URL — код выхода 2: пустой результат не выдаётся за успех.
"""
import argparse, os, sys, warnings
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, unquote

warnings.filterwarnings("ignore")
import advertools as adv
import pandas as pd


def host(d: str) -> str:
    d = d.strip().lower()
    if "://" in d:
        d = urlparse(d).netloc
    return d.strip("/").encode("idna").decode("ascii")


def sitemaps_for(h: str) -> pd.DataFrame:
    """Карты сайта через robots.txt; если там нет — стандартные адреса."""
    for src in (f"https://{h}/robots.txt", f"https://{h}/sitemap.xml", f"https://{h}/sitemap_index.xml"):
        try:
            df = adv.sitemap_to_df(src, max_workers=4)
        except Exception:
            continue
        if df is not None and "loc" in df.columns and df["loc"].notna().any():
            df["source"] = src
            return df
    return pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("domains", nargs="+")
    ap.add_argument("--out", default=".")
    ap.add_argument("--recent", type=int, default=90, help="окно «свежих» страниц, дней")
    ap.add_argument("--section", default=None, help="оставить только URL с этим фрагментом пути, напр. blog")
    a = ap.parse_args()

    frames, cut = [], datetime.now(timezone.utc) - timedelta(days=a.recent)
    for d in a.domains:
        h = host(d)
        df = sitemaps_for(h)
        if df.empty:
            print(f"{d}: карта сайта не найдена или недоступна")
            continue
        df = df[df["loc"].notna()].drop_duplicates("loc").copy()
        df["domain"] = d
        paths = df["loc"].map(lambda u: unquote(urlparse(u).path))
        df["section"] = paths.map(lambda p: (p.strip("/").split("/") or [""])[0] or "(корень)")
        df["slug"] = paths.map(lambda p: p.strip("/").split("/")[-1] if p.strip("/") else "")
        df["slug_words"] = df["slug"].str.replace(r"\.(html?|php)$", "", regex=True).str.replace(r"[-_]+", " ", regex=True)
        if "lastmod" in df.columns:
            df["lastmod"] = pd.to_datetime(df["lastmod"], utc=True, errors="coerce")
        else:
            df["lastmod"] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
        if a.section:
            df = df[df["loc"].str.contains(a.section, case=False, na=False)]
        frames.append(df[["domain", "section", "loc", "slug_words", "lastmod", "source"]])

        recent = df[df["lastmod"] >= cut]
        dated = df["lastmod"].notna().mean() if len(df) else 0
        print(f"\n== {d}: {len(df)} URL, с датой изменения {dated:.0%}, за {a.recent} дн.: {len(recent)}")
        top = df.groupby("section").agg(всего=("loc", "size"),
                                         свежих=("lastmod", lambda s: int((s >= cut).sum())))
        print(top.sort_values("всего", ascending=False).head(12).to_string())

    if not frames:
        print("Ни по одному домену не получено ни одного URL.", file=sys.stderr)
        return 2
    out = pd.concat(frames, ignore_index=True)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"inventory_{datetime.now():%Y-%m-%d}.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\nЗаписано строк: {len(out)} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
