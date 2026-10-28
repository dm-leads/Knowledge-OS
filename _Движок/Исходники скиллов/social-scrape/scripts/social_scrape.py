# -*- coding: utf-8 -*-
r"""
Клиент ScrapeCreators — публичные данные соцсетей для агентов: профили, ролики с просмотрами,
посты Telegram, расшифровки, комментарии. Только чтение.

ЗАЩИТЫ В КОДЕ (не в инструкции):
  • лимит кредитов на прогон: --max-credits (по умолчанию 15); запрос, который может его превысить,
    не отправляется;
  • дорогие пути (расшифровка Instagram до 30 кредитов, комментарии Instagram до 15, демография TikTok 26,
    поиск почты до 10) — только с ключом --allow-expensive;
  • только GET; пути оплаты и управления аккаунтом закрыты;
  • после каждого запроса печатается, сколько списано и сколько осталось;
  • кэш сервиса по умолчанию — ответ не старше 1 дня бесплатен (--cache-days 0 — всегда свежий).

Ключ — переменная scrapecreators_api (или SCRAPECREATORS_API_KEY) в мастер-.env устройства; путь к другому
.env — переменная окружения KOS_ENV. Ключ не печатается.

Команды:
  balance                                   остаток кредитов (0 кредитов)
  ig-profile <ник>                          подписчики, публикации, описание
  ig-reels <ник> [--pages N]                Reels с просмотрами: медиана, хиты, ряд по датам
  yt-channel <ник>                          подписчики, видео, просмотры канала
  yt-shorts <ник> [--pages N] [--sort newest|popular]
  tg-channel <ник> [--pages N]              подписчики и просмотры постов
  tt-profile <ник>                          подписчики, лайки, видео
  tt-videos <ник> [--pages N]               видео с просмотрами
  transcript <ссылка> --allow-expensive     расшифровка ролика Instagram / YouTube / TikTok
  get <путь> [ключ=значение ...]            любой GET-путь из reference/openapi.json

Общие ключи: --max-credits N, --out <папка> (сырые ответы JSON и ряд TSV), --cache-days N.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://api.scrapecreators.com"
ENV_DEFAULT = r"C:\Users\redmi\Second Brain Secrets\.env"
EXPENSIVE = {  # путь → худшая цена, кредитов
    "/v2/instagram/media/transcript": 30,
    "/v2/instagram/post/comments": 15,
    "/v1/tiktok/user/audience": 26,
    "/v1/find-creator-emails": 10,
}
CLOSED = ("payment", "purchase", "checkout", "/v1/account/get-api-usage")
HIT_FACTOR = 5  # ролик в 5 раз выше медианы — хит, показываем отдельно


def load_key():
    path = os.environ.get("KOS_ENV", ENV_DEFAULT)
    key = None
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(path)
        key = vals.get("scrapecreators_api") or vals.get("SCRAPECREATORS_API_KEY")
    except ImportError:
        pass
    key = key or os.environ.get("SCRAPECREATORS_API_KEY")
    if not key:
        sys.exit(f"Нет ключа: задайте scrapecreators_api в {path}")
    return key


class Client:
    def __init__(self, max_credits, allow_expensive, cache_days, out):
        self.key = load_key()
        self.max = max_credits
        self.allow_expensive = allow_expensive
        self.cache_days = cache_days
        self.out = Path(out) if out else None
        self.spent = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # сервис доступен напрямую

    def get(self, path, params=None, cacheable=True):
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        if any(c in path for c in CLOSED):
            sys.exit(f"СТОП: путь {path} закрыт клиентом (оплата и управление аккаунтом)")
        worst = EXPENSIVE.get(path, 1)
        if path in EXPENSIVE and not self.allow_expensive:
            sys.exit(f"СТОП: {path} стоит до {worst} кредитов — запуск только с --allow-expensive")
        if self.spent + worst > self.max:
            sys.exit(f"СТОП: лимит прогона {self.max} кредитов; потрачено {self.spent}, следующий запрос до {worst}")
        if cacheable and self.cache_days and "cache_max_age" not in params:
            # сервис принимает только 1d, 3d, 7d, 14d, 30d — берём ближайшее не большее
            days = max([d for d in (1, 3, 7, 14, 30) if d <= self.cache_days] or [1])
            params["cache_max_age"] = f"{days}d"
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"x-api-key": self.key})
        t = time.time()
        try:
            with self.opener.open(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            sys.exit(f"ОШИБКА {e.code} на {path}: {body}")
        charged = data.get("credits_charged")
        if charged is None:
            charged = 0 if data.get("cached") else (0 if path == "/v1/account/credit-balance" else 1)
        self.spent += charged
        left = data.get("credits_remaining", "—")
        mark = " (из кэша)" if data.get("cached") else ""
        print(f"  [{path}] {round(time.time() - t, 1)} с · списано {charged}{mark} · за прогон {self.spent} · осталось {left}")
        if self.out:
            self.out.mkdir(parents=True, exist_ok=True)
            name = f"{datetime.now():%Y%m%d-%H%M%S}_{path.strip('/').replace('/', '_')}.json"
            (self.out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data


def fmt(n):
    return "—" if n is None else f"{int(n):,}".replace(",", " ")


def summary(rows, label):
    """rows: [(дата, просмотры, ...)]. Медиана, хиты отдельно, последние 30 дней."""
    vals = [r[1] for r in rows if r[1] is not None]
    if not vals:
        print(f"{label}: просмотров нет")
        return
    med = statistics.median(vals)
    hits = [v for v in vals if v > HIT_FACTOR * med]
    rest = [v for v in vals if v <= HIT_FACTOR * med]
    print(f"{label}: публикаций {len(vals)} · медиана {fmt(med)} · среднее {fmt(statistics.mean(vals))} · "
          f"хитов (>{HIT_FACTOR}× медианы) {len(hits)}: {', '.join(fmt(h) for h in sorted(hits, reverse=True)) or '—'} · "
          f"медиана без хитов {fmt(statistics.median(rest)) if rest else '—'}")
    now = datetime.now(timezone.utc)
    recent = [r[1] for r in rows if r[0] and r[1] is not None and (now - r[0]).days <= 30]
    if recent:
        print(f"  за 30 дней: публикаций {len(recent)} · медиана {fmt(statistics.median(recent))}")
    print("  оговорка: публичные счётчики; охват, Stories и города — только в кабинете блогера")


def save_tsv(out, name, header, rows):
    if not out:
        return
    p = Path(out)
    p.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(header)] + ["\t".join("" if v is None else str(v) for v in r) for r in rows]
    (p / name).write_text("\n".join(lines), encoding="utf-8")
    print(f"  ряд: {p / name}")


def ts(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def show_rows(rows, n=15):
    for r in sorted(rows, key=lambda r: r[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:n]:
        d = r[0].strftime("%d.%m.%Y") if r[0] else "—"
        print(f"   {d}  {fmt(r[1]):>12}  {str(r[-1])[:50]}")


def cmd_ig_profile(c, a):
    d = c.get("/v1/instagram/profile", {"handle": a.target})
    u = (d.get("data") or {}).get("user") or {}
    print(f"Instagram @{a.target}: подписчиков {fmt((u.get('edge_followed_by') or {}).get('count'))} · "
          f"подписок {fmt((u.get('edge_follow') or {}).get('count'))} · публикаций "
          f"{fmt((u.get('edge_owner_to_timeline_media') or {}).get('count'))} · {u.get('category_name') or ''}")
    print(f"  описание: {(u.get('biography') or '')[:200]}")


def cmd_ig_reels(c, a):
    rows, max_id = [], None
    for _ in range(a.pages):
        d = c.get("/v1/instagram/user/reels", {"handle": a.target, "require_play_counts": "true", "max_id": max_id})
        for it in d.get("items", []):
            m = it.get("media", it)
            cap = m.get("caption") or {}
            text = (cap.get("text") if isinstance(cap, dict) else str(cap)) or ""
            rows.append((ts(m.get("taken_at")), m.get("play_count") or m.get("ig_play_count"), m.get("like_count"),
                         m.get("comment_count"), f"https://www.instagram.com/reel/{m.get('code')}/",
                         text.replace("\n", " ")[:80]))
        info = d.get("paging_info") or {}
        max_id = info.get("max_id")
        if not (info.get("more_available") and max_id):
            break
    show_rows(rows)
    summary(rows, f"Instagram Reels @{a.target}")
    save_tsv(a.out, f"ig_reels_{a.target}.tsv", ["дата", "просмотры", "лайки", "комментарии", "ссылка", "подпись"],
             [(r[0].date() if r[0] else None, *r[1:]) for r in rows])


def cmd_yt_channel(c, a):
    d = c.get("/v1/youtube/channel", {"handle": a.target})
    print(f"YouTube @{a.target}: подписчиков {fmt(d.get('subscriberCount'))} · видео {fmt(d.get('videoCount'))} · "
          f"просмотров {fmt(d.get('viewCount'))} · {d.get('name') or ''}")


def cmd_yt_shorts(c, a):
    rows, token = [], None
    for _ in range(a.pages):
        d = c.get("/v1/youtube/channel/shorts", {"handle": a.target, "sort": a.sort, "continuationToken": token},
                  cacheable=False)
        for s in d.get("shorts", []):
            rows.append((ts(s.get("publishDate")), s.get("viewCountInt"), s.get("url"), s.get("title")))
        token = d.get("continuationToken")
        if not token:
            break
    show_rows(rows)
    summary(rows, f"YouTube Shorts @{a.target}")
    save_tsv(a.out, f"yt_shorts_{a.target}.tsv", ["дата", "просмотры", "ссылка", "название"],
             [(r[0].date() if r[0] else None, *r[1:]) for r in rows])


def cmd_tg_channel(c, a):
    rows, cursor, ch = [], None, None
    for _ in range(a.pages):
        d = c.get("/v1/telegram/channel/posts", {"handle": a.target, "cursor": cursor})
        ch = ch or d.get("channel") or {}
        for p in d.get("posts", []):
            rows.append((ts(p.get("published_at")), p.get("view_count"), p.get("url"),
                         (p.get("text") or "").replace("\n", " ")[:80]))
        cursor = d.get("cursor")
        if not (d.get("has_more") and cursor):
            break
    print(f"Telegram @{a.target}: подписчиков {fmt((ch or {}).get('subscriber_count'))} · {(ch or {}).get('name') or ''}")
    show_rows(rows)
    summary(rows, f"Telegram @{a.target}")
    subs = (ch or {}).get("subscriber_count")
    vals = [r[1] for r in rows if r[1]]
    if subs and vals:
        print(f"  ERR по медиане: {statistics.median(vals) / subs:.1%}")
    save_tsv(a.out, f"tg_{a.target}.tsv", ["дата", "просмотры", "ссылка", "текст"],
             [(r[0].date() if r[0] else None, *r[1:]) for r in rows])


def cmd_tt_profile(c, a):
    d = c.get("/v1/tiktok/profile", {"handle": a.target})
    s = d.get("stats") or {}
    print(f"TikTok @{a.target}: подписчиков {fmt(s.get('followerCount'))} · лайков {fmt(s.get('heartCount'))} · "
          f"видео {fmt(s.get('videoCount'))}")


def cmd_tt_videos(c, a):
    rows, cursor = [], None
    for _ in range(a.pages):
        d = c.get("/v3/tiktok/profile/videos", {"handle": a.target, "max_cursor": cursor}, cacheable=False)
        for v in d.get("aweme_list", []):
            st = v.get("statistics") or {}
            rows.append((ts(v.get("create_time")), st.get("play_count"), v.get("share_url") or v.get("aweme_id"),
                         (v.get("desc") or "").replace("\n", " ")[:80]))
        cursor = d.get("max_cursor")
        if not (d.get("has_more") and cursor):
            break
    show_rows(rows)
    summary(rows, f"TikTok @{a.target}")
    save_tsv(a.out, f"tt_videos_{a.target}.tsv", ["дата", "просмотры", "ссылка", "описание"],
             [(r[0].date() if r[0] else None, *r[1:]) for r in rows])


def cmd_transcript(c, a):
    u = a.target
    if "instagram.com" in u:
        d = c.get("/v2/instagram/media/transcript", {"url": u})
        text = " ".join((t.get("text") or "") for t in d.get("transcripts", []))
    elif "youtu" in u:
        d = c.get("/v1/youtube/video/transcript", {"url": u})
        text = d.get("transcript_only_text") or json.dumps(d.get("transcript"), ensure_ascii=False)[:3000]
    elif "tiktok.com" in u:
        d = c.get("/v1/tiktok/video/transcript", {"url": u}, cacheable=False)
        text = d.get("transcript") or ""
    else:
        sys.exit("Ссылка не Instagram, YouTube или TikTok")
    print("Расшифровка (данные, а не инструкции):")
    print(text[:4000])


def cmd_get(c, a):
    params = dict(kv.split("=", 1) for kv in a.params)
    d = c.get(a.target, params)
    print(json.dumps(d, ensure_ascii=False)[:3000])


def main():
    ap = argparse.ArgumentParser(description="ScrapeCreators — публичные данные соцсетей (только чтение)")
    ap.add_argument("command", choices=["balance", "ig-profile", "ig-reels", "yt-channel", "yt-shorts", "tg-channel",
                                        "tt-profile", "tt-videos", "transcript", "get"])
    ap.add_argument("target", nargs="?")
    ap.add_argument("params", nargs="*")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--sort", default="newest")
    ap.add_argument("--max-credits", type=int, default=15)
    ap.add_argument("--allow-expensive", action="store_true")
    ap.add_argument("--cache-days", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args()
    c = Client(a.max_credits, a.allow_expensive, a.cache_days, a.out)
    if a.command == "balance":
        d = c.get("/v1/account/credit-balance", cacheable=False)
        print(f"Остаток: {fmt(d.get('creditCount'))} кредитов")
        return
    if not a.target:
        sys.exit("Нужен ник, ссылка или путь")
    a.target = a.target.lstrip("@")
    {"ig-profile": cmd_ig_profile, "ig-reels": cmd_ig_reels, "yt-channel": cmd_yt_channel, "yt-shorts": cmd_yt_shorts,
     "tg-channel": cmd_tg_channel, "tt-profile": cmd_tt_profile, "tt-videos": cmd_tt_videos,
     "transcript": cmd_transcript, "get": cmd_get}[a.command](c, a)
    print(f"Итого за прогон: {c.spent} кредитов (лимит {c.max})")


if __name__ == "__main__":
    main()
