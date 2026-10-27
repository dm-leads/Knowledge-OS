"""Провайдер `template` (Т30.1): обложка и схема шаблоном, 0 ₽, без нейросети.

Обложка — заголовок статьи и направление на однотонном фоне; схема — шаги из пунктов нумерованного списка раздела.
Файл — SVG: собирается кодом без браузера и без установки. Свой шаблон проекта (`images.template`) — SVG с
подстановками `{title}`, `{direction}`, `{steps}`.
"""
from __future__ import annotations

import re
from html import escape
from pathlib import Path

COVER = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
<rect width="1200" height="630" fill="#1f3b4d"/>
<text x="80" y="120" fill="#9fc3d6" font-family="Arial, sans-serif" font-size="32">{direction}</text>
{title}
</svg>"""
STEP = re.compile(r"^\s*\d+[.)]\s+(.+)$", re.M)


def _lines(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return lines + ([line] if line else [])


def steps_of(text: str) -> list[str]:
    return [re.sub(r"[*_`]", "", s).strip() for s in STEP.findall(text)]


class TemplateProvider:
    name = "template"

    def __init__(self, instance):
        self.instance = instance
        custom = instance.p["images"]["template"]
        self.custom = (instance.root / custom).read_text(encoding="utf-8") if custom else ""

    def price(self, request) -> float:
        return 0.0

    def _cover(self, request) -> str:
        title = "\n".join(f'<text x="80" y="{230 + 70 * i}" fill="#ffffff" font-family="Arial, sans-serif" '
                          f'font-size="56" font-weight="bold">{escape(line)}</text>'
                          for i, line in enumerate(_lines(request.topic, 34)[:5]))
        return COVER.format(direction=escape(request.extra.get("direction", "")), title=title)

    def _scheme(self, request) -> str:
        steps = steps_of(request.text)[:8] or [request.heading]
        height = 120 + 110 * len(steps)
        rows = []
        for i, step in enumerate(steps):
            y = 80 + 110 * i
            rows.append(f'<rect x="60" y="{y}" width="1080" height="80" rx="12" fill="#eef4f7" stroke="#1f3b4d"/>'
                        f'<text x="100" y="{y + 50}" font-family="Arial, sans-serif" font-size="30" fill="#1f3b4d">'
                        f'{i + 1}. {escape(step[:70])}</text>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}">'
                f'<rect width="1200" height="{height}" fill="#ffffff"/>' + "".join(rows) + "</svg>")

    def make(self, request, path: Path) -> dict:
        if self.custom:
            svg = self.custom.format(title=escape(request.topic), direction=escape(request.extra.get("direction", "")),
                                     steps="".join(f"<li>{escape(s)}</li>" for s in steps_of(request.text)))
        else:
            svg = self._cover(request) if request.kind == "cover" else self._scheme(request)
        target = Path(path).with_suffix(".svg")
        target.write_text(svg, encoding="utf-8", newline="\n")
        return {"file": str(target), "url": "", "price": 0.0}
