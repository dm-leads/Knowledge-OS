"""Вёрстка живой страницы браузером (Т22.4, Р19): необязательная зависимость Playwright.

На каждой ширине `publish.widths`: запросы к доменам `publish.counter_domains` блокируются до отправки и пишутся в
журнал сети отчёта как «заблокирован» — проверка не попадает в аналитику; нет горизонтальной прокрутки
(`scrollWidth ≤ innerWidth`); правый край таблиц не выходит за колонку; обязательные элементы
(`assemble.required_elements`) видимы; снимок страницы — в `reports/<дата>/layout/`. Браузера нет — пункт
«не подключено», итог не «прошло».
"""
from __future__ import annotations

from pathlib import Path

from ..core.urls import host_unicode, slug_of

OFF = "не подключено"

TABLES_JS = """() => Array.from(document.querySelectorAll('table')).filter(t => {
  const col = t.parentElement ? t.parentElement.getBoundingClientRect().right : window.innerWidth;
  let p = t.parentElement, scroll = false;
  while (p) { const s = getComputedStyle(p); if (s.overflowX === 'auto' || s.overflowX === 'scroll') { scroll = true; break; } p = p.parentElement; }
  return !scroll && t.getBoundingClientRect().right > Math.min(col, window.innerWidth) + 1;
}).length"""


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def check(instance, url: str, *, out_dir: Path | None = None) -> dict:
    pub = instance.p["publish"]
    if not pub["browser"]:
        return {"status": OFF, "why": "publish.browser: false"}
    if not available():
        return {"status": OFF, "why": "Playwright не установлен: pip install content_engine[browser]"}
    from playwright.sync_api import sync_playwright
    counters = {host_unicode(d) for d in pub["counter_domains"]}
    required = instance.p["assemble"]["required_elements"]
    out_dir = out_dir or instance.reports_dir("layout")
    out_dir.mkdir(parents=True, exist_ok=True)
    widths, network, problems, errors = [], [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        reached = []
        try:
            for width in pub["widths"]:
                # сервис-воркер обходит page.route — запрещён, чтобы ни один запрос счётчика не ушёл мимо перехвата
                context = browser.new_context(viewport={"width": width, "height": 900}, service_workers="block")
                page = context.new_page()
                page.on("requestfinished", lambda req: reached.append(req.url) if _counter(req.url, counters)
                        else None)

                def route(r, request=None, width=width):     # Playwright передаёт (route, request)
                    if _counter(r.request.url, counters):
                        network.append({"width": width, "url": r.request.url, "status": "заблокирован"})
                        return r.abort()
                    return r.continue_()
                page.route("**/*", route)
                page.goto(url, wait_until="load", timeout=instance.p["prepare"]["page_timeout"] * 3000)
                no_scroll = page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                tables_out = page.evaluate(TABLES_JS)
                hidden = [sel for sel in required if not page.locator(_css(sel)).first.is_visible()]
                shot = out_dir / f"{slug_of(url) or 'page'}-{width}.png"
                try:
                    page.screenshot(path=str(shot), full_page=True)
                except Exception as exc:          # снимок — доказательство, а не сама проверка: не роняет команду
                    errors.append(f"{width}px: снимок не снят — {type(exc).__name__}")
                    shot = ""
                ok = no_scroll and not tables_out and not hidden
                widths.append({"width": width, "ok": ok, "horizontal_scroll": not no_scroll,
                               "tables_out": tables_out, "hidden": hidden, "screenshot": str(shot)})
                if not ok:
                    problems.append(f"{width}px: " + "; ".join(
                        x for x in (("горизонтальная прокрутка" if not no_scroll else ""),
                                    (f"таблиц за колонкой {tables_out}" if tables_out else ""),
                                    (f"не видны {hidden}" if hidden else "")) if x))
                context.close()
        finally:
            browser.close()
    if reached:
        problems.append(f"запросы к счётчикам дошли до сети: {len(reached)}")
    status = "не прошло" if problems else ("не проверено" if errors else "прошло")
    return {"status": status, "widths": widths, "problems": problems + errors, "network": network,
            "counters_reached": len(reached), "why": "; ".join(errors)}


def _counter(url: str, counters: set) -> bool:
    host = host_unicode(url.split("/")[2]) if "//" in url else ""
    return any(host == c or host.endswith("." + c) for c in counters)


def _css(selector: str) -> str:
    return selector.strip()
