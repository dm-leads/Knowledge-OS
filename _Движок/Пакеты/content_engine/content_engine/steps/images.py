"""Картинки (В5, Т30): `content-engine images plan | make | upload | insert <id>` и `images backfill`.

- `plan <id>` — места картинок (Д7: главное — картинки внутри текста). Обложка — по `images.cover`; в теле при
  `fill_rule: top` — столько, сколько у лидеров выдачи (`top.json`, Т18.6), по одной после H2, начиная с самых длинных
  разделов, без двух подряд и не в FAQ; при `after_h2` — места из списка. Вид места: раздел совпал с библиотекой
  готовых фото → `product`; раздел — шаги нумерованным списком → `scheme`; иначе → `illustration`. Провайдер вида —
  `images.providers`; `none` — место пропускается с пометкой. Итог — `images.json` и «в теле N из цели M».
- `make <id>` — файлы: бесплатные провайдеры сразу, платные — одной суммой через бюджет-гейт (`--confirm-spend`, без
  человека — ноль платных вызовов); отказ по кадру — заглушка, сборка не падает; сжатие до `images.max_kb` (Pillow).
- `upload <id> [--apply]` — в хранилище сайта: запись вовне с правом `images.allow: write`, снимок «до», подтверждение
  «адрес отвечает», откат — `publish rollback`. Загрузчика нет — загружает человек по пакету.
- `insert <id>` — картинки с адресами в текст: `![alt](адрес)` после первого абзаца своего раздела, обложка — полем
  `image` шапки. Текст меняется — дальше гейт статьи заново; вычитку это не сбрасывает (сумма вычитки — без картинок).
- `backfill [--apply]` — опубликованные статьи без картинок в теле: добор ставится в очередь прогоном с id, снимком
  страницы «до» и откатом всего прогона `publish rollback --run <id>`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core import actions as actions_mod
from ..core import budget, rights, snapshots, store
from ..core.errors import EngineError, GateRefused
from ..images.base import Request, provider, uploader
from ..images.template import steps_of
from .gate_article import _sections

PLAN = "images.json"
MAX_PRODUCT = 2          # фото продукта в статье, не больше: статья — не каталог, остальное — иллюстрации и схемы
IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")


def _plan_path(instance, tid: str) -> Path:
    return instance.brief_dir(tid) / PLAN


def load_plan(instance, tid: str) -> dict:
    path = _plan_path(instance, tid)
    if not path.is_file():
        raise GateRefused(f"тема {tid}: плана картинок нет — `images plan {tid}`")
    return json.loads(path.read_text(encoding="utf-8"))


def save_plan(instance, tid: str, plan: dict) -> str:
    path = _plan_path(instance, tid)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    if json.loads(path.read_text(encoding="utf-8")) != plan:
        raise EngineError(f"{PLAN}: записан — прочитан другим")
    return f"{PLAN}: мест {len(plan['images'])}"


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        if block.strip() and not block.lstrip().startswith(("#", "!", "|", "-", "*")):
            return block.strip()
    return ""


def make_plan(instance, tid: str) -> dict:
    folder = instance.brief_dir(tid)
    article = folder / "article.md"
    if not article.is_file():
        raise GateRefused(f"тема {tid}: текста нет — места картинок ставятся по тексту")
    from ..adapters.corpus import split_front_matter
    meta, body = split_front_matter(article.read_text(encoding="utf-8"))
    im, p = instance.p["images"], instance.p
    skip = (p["gate_article"]["faq_heading"].lower(), p["corpus"]["toc_marker"].lower())
    h1 = next((t for level, t, _ in _sections(body) if level == 1), meta.get("title", ""))
    h2 = [(t, txt) for level, t, txt in _sections(body) if level == 2 and not t.lower().startswith(skip)]
    top = store.read_json(folder / "top.json", "topic_top") if (folder / "top.json").is_file() else {}
    target = int(((top.get("numbers") or {}).get("images") or {}).get("value") or 0)
    if im["fill_rule"] == "after_h2":
        chosen = sorted(n - 1 for n in im["after_h2"] if 0 < n <= len(h2))
        target = len(chosen)
    else:
        order = sorted(range(len(h2)), key=lambda i: (-len(h2[i][1]), i))
        chosen = []
        for i in order:
            if len(chosen) >= target:
                break
            if all(abs(i - j) > 1 for j in chosen):
                chosen.append(i)
        for i in order:                                  # разделов мало — «не подряд» уступает цели
            if len(chosen) >= target:
                break
            if i not in chosen:
                chosen.append(i)
        chosen.sort()
    library = None
    if "library" in im["providers"].values():
        from ..images.library import LibraryProvider
        library = LibraryProvider(instance)
    used, items, notes = set(), [], []
    product_left = MAX_PRODUCT
    from .prepare import topic_of
    direction = topic_of(instance, tid).get("direction", "")
    if im["cover"]:
        items.append({"n": 1, "kind": "cover", "place": "обложка", "alt": h1, "heading": h1, "ref": ""})
    for i in chosen:
        heading, text = h2[i]
        lead = f"{heading}\n{_first_paragraph(text)}"
        row = (library.match(lead, used) if library and im["providers"]["product"] == "library" and product_left > 0
               else None)
        if row:
            used.add(row["ref"])
            product_left -= 1
            items.append({"kind": "product", "place": heading, "alt": row["alt"] or heading, "heading": heading,
                          "ref": row["ref"]})
        elif len(steps_of(text)) >= 2:
            items.append({"kind": "scheme", "place": heading, "alt": f"Схема: {heading}", "heading": heading, "ref": ""})
        else:
            items.append({"kind": "illustration", "place": heading, "alt": heading, "heading": heading, "ref": ""})
    for n, item in enumerate(items, 1):
        spec = im["providers"].get(item["kind"], "none")
        item.update(n=n, provider=spec, status="пропущено: провайдер none" if spec == "none" else "план", file="",
                    url="", source="", license="", author="", price=None, direction=direction,
                    prompt="")                       # промпт генератору — агент может дописать до `images make`
        if not str(item["alt"]).strip():
            notes.append(f"место {n} ({item['place']}): нет alt — гейт статьи проверит `image_alt`")
    body_items = [x for x in items if x["kind"] != "cover" and not x["status"].startswith("пропущено")]
    plan = {"schema_version": 1, "kind": "images_plan", "topic": tid, "target": target, "images": items}
    text = save_plan(instance, tid, plan)
    return {"plan": plan, "line": f"в теле {len(body_items)} картинок из цели {target}; {text}", "notes": notes}


def _request(item: dict, plan_topic: str, text: str) -> Request:
    return Request(item["kind"], plan_topic, item["heading"], text, item["alt"],
                   {"ref": item.get("ref", ""), "direction": item.get("direction", ""), "prompt": item.get("prompt", "")})


PHOTOS = "photos.jsonl"
PHOTO_FIELDS = ("place", "url", "author", "license", "source", "alt")


def photo_task(instance, tid: str) -> Path:
    """Т30.3: фото из сети машина не ищет — задание агенту: кадр, автор, лицензия, стоп-лист категорий; агент пишет
    строки в отдельный файл `photos.jsonl`, план картинок не трогает. `images photo <id> --merge` проверяет строки и
    вливает их в план. Гейт статьи (`image_credits`) требует автора и лицензию у каждого фото из сети."""
    from ..core import tasks
    plan = load_plan(instance, tid)
    stop = instance.p["images"]["stop_categories"]
    places = [i["place"] for i in plan["images"] if i["kind"] != "cover"]
    body = ["## Задача", "",
            f"Подобрать фото из сети для статьи темы {tid}, если готовых картинок проекта не хватает. Только кадры с "
            "явной лицензией (своя, CC с указанием автора, фотобанк с правом публикации). Каждое фото — строкой JSON "
            f"в `{instance.brief_dir(tid) / PHOTOS}`: place (заголовок раздела из списка ниже), url, author, license, "
            f"source (страница кадра), alt. Файл плана `{PLAN}` не править: строки вольёт `images photo {tid} --merge`."
            " Без автора или лицензии фото не ставится.", "",
            "## Места", *([f"- {p}" for p in places] or ["- мест нет — сначала `images plan`"]), "",
            "## Не годятся", *([f"- {c}" for c in stop] or ["- стоп-лист категорий не задан (images.stop_categories)"]),
            "", "Править готовую картинку (текст, цифры, предмет) — генеративным редактором (навык `image-edit`), "
            "а не заплаткой в графическом редакторе."]
    return tasks.write_task(instance, tid, "planner", "photo", "\n".join(body), result=PHOTOS,
                            schema='{"place": "<H2>", "url": "", "author": "", "license": "", "source": "", "alt": ""}')


def merge_photos(instance, tid: str) -> dict:
    """Строки фото агента → план картинок: каждое поле заполнено, место — раздел плана, адрес — не дубль."""
    plan = load_plan(instance, tid)
    path = instance.brief_dir(tid) / PHOTOS
    if not path.is_file():
        raise GateRefused(f"тема {tid}: строк фото нет — {path}")
    places = {i["place"] for i in plan["images"] if i["kind"] != "cover"}
    known = {i.get("url") for i in plan["images"]}
    added, problems = 0, []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            problems.append(f"строка {n}: не JSON")
            continue
        empty = [f for f in PHOTO_FIELDS if not str(row.get(f) or "").strip()]
        if empty:
            problems.append(f"строка {n}: пусто {empty}")
        elif row["place"] not in places:
            problems.append(f"строка {n}: раздела «{row['place']}» нет в плане")
        elif not str(row["url"]).startswith("http"):
            problems.append(f"строка {n}: адрес не http")
        elif row["url"] in known:
            continue
        else:
            known.add(row["url"])
            plan["images"].append({"n": len(plan["images"]) + 1, "kind": "photo", "place": row["place"],
                                   "heading": row["place"], "alt": row["alt"], "ref": "", "provider": "web",
                                   "status": "готово", "file": "", "url": row["url"], "source": row["source"],
                                   "license": row["license"], "author": row["author"], "price": 0.0,
                                   "direction": "", "prompt": ""})
            added += 1
    text = save_plan(instance, tid, plan)
    return {"line": f"фото добавлено {added}" + (f"; отклонено {len(problems)}: " + "; ".join(problems) if problems
                                                  else "") + f"; {text}", "problems": problems}


def compress(instance, path: Path) -> str:
    """До `images.max_kb`: растровая картинка пересохраняется JPEG с понижением качества; SVG не трогается."""
    limit = instance.p["images"]["max_kb"] * 1024
    if path.suffix.lower() == ".svg" or path.stat().st_size <= limit:
        return str(path)
    try:
        from PIL import Image
    except ImportError:
        return str(path) + " (не сжато: нет Pillow — pip install content_engine[images])"
    target = path.with_suffix(".jpg")
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        for quality in (85, 75, 65, 55, 45):
            rgb.save(target, "JPEG", quality=quality, optimize=True)
            if target.stat().st_size <= limit:
                break
    if target != path:
        path.unlink()
    return str(target)


def make(instance, tid: str, *, confirm=None, unattended=False) -> dict:
    plan = load_plan(instance, tid)
    folder = instance.brief_dir(tid) / "images"
    folder.mkdir(exist_ok=True)
    article = (instance.brief_dir(tid) / "article.md").read_text(encoding="utf-8")
    sections = {t: txt for level, t, txt in _sections(article) if level == 2}
    todo = [i for i in plan["images"] if i["status"] == "план" or i["status"].startswith("отказ")]
    providers = {spec: provider(instance, i["kind"]) for i in todo for spec in [i["provider"]]}
    paid = [i for i in todo if i["provider"].startswith("module:")]
    total = round(sum(float(providers[i["provider"]].price(_request(i, plan["topic"], sections.get(i["place"], ""))))
                      for i in paid), 2)

    def run(items):
        for item in items:
            prov = providers[item["provider"]]
            try:
                got = prov.make(_request(item, plan["topic"], sections.get(item["place"], "")),
                                folder / f"{item['n']:02d}-{item['kind']}")
                item.update(file=compress(instance, Path(got["file"])) if got.get("file") else "",
                            url=got.get("url", ""), price=got.get("price"), status="готово",
                            source=item["provider"])
            except Exception as exc:                 # отказ провайдера по кадру — заглушка, сборка не падает
                item.update(status=f"отказ: {type(exc).__name__}: {exc}"[:200])
        return items

    run([i for i in todo if i not in paid])
    save_plan(instance, tid, plan)                   # бесплатные готовы, даже если платные остановит бюджет-гейт
    if paid:                                     # провайдер проекта — платный: через гейт и при цене 0
        budget.paid_call(instance, tid, "картинки", total, lambda: run(paid), confirm=confirm,
                         unattended=unattended, what=f"картинки {len(paid)} шт.",
                         source=f"цены провайдеров на {instance.today.isoformat()}")
    text = save_plan(instance, tid, plan)
    done = sum(1 for i in plan["images"] if i["status"] == "готово")
    return {"line": f"картинок готово {done} из {len(plan['images'])}; платных {len(paid)} на {total:.2f} ₽; {text}",
            "plan": plan}


def upload(instance, tid: str, *, apply: bool) -> dict:
    plan = load_plan(instance, tid)
    items = [i for i in plan["images"] if i["status"] == "готово" and i["file"] and not i["url"]]
    if not items:
        return {"line": "загружать нечего: у готовых картинок уже есть адреса"}
    up = uploader(instance)
    if up is None:
        return {"line": "загрузчика нет (images.upload: none, провайдер не загружает) — загружает человек; файлы: "
                        + ", ".join(Path(i["file"]).name for i in items)}
    planned = []
    for item in items:
        url = up.target(item["file"], tid)

        def do(item=item, url=url):
            return up.upload(item["file"], url)

        def verify(_, item=item, url=url):
            ok, detail = up.read_back(url)
            if ok:
                item["url"] = url
            return ok, detail

        def snap(url=url, item=item):
            state = dict(up.snapshot(url))
            state.setdefault("read_from", url)
            state["meta"] = {**(state.get("meta") or {}), "file": item["file"], "url": url}
            return state

        planned.append(actions_mod.Action("image_upload", url, "PUT", f"картинка {item['n']} ({item['place']})",
                                          "read_back", ("images", "write"), do=do, verify=verify, snapshot=snap,
                                          meta={"independent": True}))
    allowed, refused = rights.split(instance, planned)
    if apply and refused and not allowed:
        from ..core.errors import RightsRefused
        raise RightsRefused("загрузка не выполнена: " + "; ".join(refused[:1]))
    lines = [f"не в плане — {r}" for r in refused]
    try:
        out = actions_mod.run(instance, tid, allowed, apply=apply, echo=lambda s: lines.append(s.strip()))
    finally:
        save_plan(instance, tid, plan)
    if apply and allowed:
        lines.append(f"загрузка: {out.line()}; откат — `publish rollback {tid} --apply`")
    return {"line": "; ".join(lines)}


def insert(instance, tid: str) -> dict:
    plan = load_plan(instance, tid)
    path = instance.brief_dir(tid) / "article.md"
    text = path.read_text(encoding="utf-8")
    added = 0
    for item in plan["images"]:
        if item["status"] != "готово" or not item["url"] or item["url"] in text:
            continue
        if item["kind"] == "cover":
            if text.startswith("---") and "\nimage:" not in text.split("\n---", 1)[0]:
                text = text.replace("\n---", f"\nimage: {item['url']}\n---", 1)
                added += 1
            continue
        heading = re.compile(r"^##\s+" + re.escape(item["place"]) + r"\s*$", re.M)
        m = heading.search(text)
        if not m:
            continue
        rest = text[m.end():]
        para = re.search(r"\n\s*\n(.+?)(\n\s*\n|$)", rest, re.S)
        cut = m.end() + (para.end(1) if para else 0)
        text = text[:cut] + f"\n\n![{item['alt']}]({item['url']})" + text[cut:]
        added += 1
    if added:
        path.write_text(text, encoding="utf-8", newline="\n")
    return {"line": f"вставлено картинок: {added}" + (f"; дальше — `gate article --topic {tid}`" if added else "")}


def backfill(instance, *, apply: bool) -> dict:
    from ..adapters.page_fetch import PageFetcher
    from ..core.htmldoc import HtmlDoc, _content_images
    fetcher = PageFetcher.for_own_site(instance)
    run_id = snapshots.new_run_id(instance, "backfill-")
    queue = instance.root / "data" / "backfill" / run_id
    planned, lines = [], []
    for page in instance.corpus()[0]:
        if not page.url:
            continue
        try:
            got = fetcher.get(page.url if page.url.startswith("http") else "https://" + page.url)
        except Exception as exc:
            lines.append(f"{page.slug}: не прочитана — {exc}")
            continue
        if got.status != 200:
            continue
        doc = HtmlDoc(got.text)
        if _content_images(doc.main()[0]):
            continue
        path = queue / f"{page.slug}.json"

        def do(path=path, page=page):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"slug": page.slug, "url": page.url, "run": run_id, "status": "добор"},
                                       ensure_ascii=False), encoding="utf-8")
            return path

        planned.append(actions_mod.Action(
            "image_backfill", page.url, "queue", f"добор картинок «{page.title}»", "read_back", ("images", "files"),
            do=do, verify=lambda _, path=path: (path.is_file(), "добор в очереди"),
            snapshot=lambda html=got.text, url=page.url, path=path: {"exists": True, "content": html[:200000],
                                                                      "read_from": url, "meta": {"queued": str(path)}}))
    if not planned:
        return {"line": "статей без картинок в теле нет" + ("; " + "; ".join(lines) if lines else ""), "run": ""}
    out = actions_mod.run(instance, "backfill", planned, apply=apply, run_id=run_id,
                          echo=lambda s: lines.append(s.strip()))
    return {"line": f"статей без картинок: {len(planned)}; прогон {run_id if apply else '—'}; " + "; ".join(lines[-3:])
            + (f"; откат прогона — `publish rollback --run {run_id} --apply`" if apply else ""),
            "run": run_id if apply and out.confirmed else ""}


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("images", help="картинки: plan | make | upload | insert | backfill")
    actions = parser.add_subparsers(dest="action", required=True)
    for name, text in (("plan", "места картинок по тексту и разбору топа"), ("make", "сделать файлы картинок"),
                       ("photo", "задание агенту: фото из сети с автором и лицензией"),
                       ("upload", "загрузить в хранилище сайта"), ("insert", "вставить картинки в текст"),
                       ("backfill", "добор картинок опубликованным статьям")):
        p = actions.add_parser(name, help=text)
        add_profile(p)
        if name != "backfill":
            p.add_argument("id")
        if name == "make":
            budget.add_flags(p)
        if name in ("upload", "backfill"):
            p.add_argument("--apply", action="store_true")
        if name == "photo":
            p.add_argument("--merge", action="store_true", help="влить строки photos.jsonl в план картинок")
        p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "plan":
        out = make_plan(instance, args.id)
        print(out["line"])
        for item in out["plan"]["images"]:
            print(f"  {item['n']}. {item['kind']} — {item['place']} ({item['provider']}; {item['status']})")
        for note in out["notes"]:
            print(f"  ! {note}")
        return 0
    if args.action == "photo":
        if args.merge:
            out = merge_photos(instance, args.id)
            print(out["line"])
            return 1 if out["problems"] else 0
        print(f"задание: {photo_task(instance, args.id)}")
        return 0
    if args.action == "make":
        print(make(instance, args.id, confirm=args.confirm_spend, unattended=args.unattended)["line"])
        return 0
    if args.action == "upload":
        print(upload(instance, args.id, apply=args.apply)["line"])
        return 0
    if args.action == "insert":
        print(insert(instance, args.id)["line"])
        return 0
    print(backfill(instance, apply=args.apply)["line"])
    return 0
