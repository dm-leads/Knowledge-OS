"""Серая строка мелким шрифтом под кнопками Zero Block (сноска к покупке: доставка, оплата, гарантия) — в ЧЕРНОВИКЕ.

Пример: условия доставки, которые банк требует для интернет-эквайринга, без акцента на первом экране товара.
Ставить во ВСЕ ценовые блоки страницы (видимый и скрытые: прошлые акции, без акции), чтобы при смене акции
строка не пропала. Видимость блоков не меняется, публикации нет.

Место считается по кнопкам самого блока на каждой ширине (320/480/640/960/1200): строка на 10–12 px ниже нижней
кнопки, слева — от левой кнопки; высота артборда растёт, если строка не помещается.
Защиты в коде: снимок блока «до» обязателен (пишется здесь же); если ниже кнопок есть другой видимый элемент — блок
пропускается с причиной; блок, где строка уже есть, — пропуск; после записи обратное чтение и сверка.
Запуск:
  TILDA_MCP_VAR=<имя переменной с токеном>, NOTE_FILE=<файл с текстом строки, HTML, переносы — <br />>
  python tilda_zero_note_under_buttons.py <projectid> <папка снимков> <pageid>:<rec>[,<rec>...] [<pageid>:<rec>,...]
  --dry  — только расчёт и снимки, без записи
"""
import json
import math
import os
import sys
import time
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tilda_promo_price import Stop, connect, save, tool  # noqa: E402

TEXT = Path(os.environ["NOTE_FILE"]).read_text(encoding="utf-8").strip()  # перенос строки — <br /> (так хранит Тильда)
LINES = TEXT.split("<br />")
MARK = LINES[0][:25]  # строка уже есть в блоке — пропуск
BPS = ("", "-res-960", "-res-640", "-res-480", "-res-320")  # от широкого к узкому
FS, LH = 12, 1.45
_seq = [0]


def eff(obj, key, i):
    """Значение ключа на ширине BPS[i] с наследованием от более широких."""
    for bp in reversed(BPS[: i + 1]):
        v = obj.get(key + bp)
        if v not in (None, ""):
            return v
    return None


def hidden_at(e, i):
    return eff(e, "hidden", i) == "y"


def text_h(width):
    lines = sum(math.ceil(len(s) * 0.6 * FS / width) for s in LINES)
    return int(lines * FS * LH) + 2


def new_id():
    _seq[0] += 1
    return f"{int(time.time() * 1000)}{_seq[0]:06d}"


def plan(z):
    els = {k: e for k, e in z.items() if k.isdigit()}
    if any(MARK in (e.get("text") or "") for e in els.values()):
        return None, "строка уже есть"
    el = {"elem_id": new_id(), "elem_type": "text", "layer": "Сноска под кнопками", "text": TEXT,
          "fontfamily": "var(--t-text-font,Arial)", "fontsize": str(FS), "fontweight": "400", "lineheight": str(LH),
          "color": "#a6a6a6", "textfit": "autoheight", "hidden": "n", "zindex": str(max(int(e.get("zindex") or 0)
                                                                                      for e in els.values()) + 1),
          "leftunits": "px", "topunits": "px", "widthunits": "px", "heightunits": "px"}
    ab = {}
    for i, bp in enumerate(BPS):
        btns = [e for e in els.values() if e.get("elem_type") == "button" and not hidden_at(e, i)]
        if not btns:
            return None, f"нет видимых кнопок на ширине {bp or 'десктоп'}"
        tops = [float(eff(b, "top", i)) for b in btns]
        bottom = max(t + float(eff(b, "height", i) or 60) for t, b in zip(tops, btns))
        left = min(float(eff(b, "left", i)) for b in btns)
        right = max(float(eff(b, "left", i)) + float(eff(b, "width", i)) for b in btns)
        cont = eff(btns[0], "container", i) or ""
        for k, e in els.items():  # ниже кнопок ничего видимого быть не должно
            if e in btns or hidden_at(e, i) or eff(e, "top", i) is None:
                continue
            if float(eff(e, "top", i)) >= bottom - 2 and e.get("elem_type") not in ("gallery",):
                return None, f"элемент {k} ({e.get('elem_type')}) ниже кнопок на ширине {bp or 'десктоп'}"
        width = max(int(right - left), 275)
        top = int(bottom) + (10 if i < 2 else 12)
        h = text_h(width)
        el["top" + bp], el["left" + bp], el["width" + bp], el["height" + bp] = str(top), str(int(left)), str(width), str(h)
        if cont:
            el["container" + bp] = cont
        need = top + h + 18
        cur = float(eff(z, "ab_height", i) or 0)
        if need > cur:
            ab["ab_height" + bp] = str(need)
    z[str(max(int(k) for k in els) + 1)] = el
    z.update(ab)
    return z, f"строка добавлена, высота: {ab or 'без изменений'}"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry"]
    dry = "--dry" in sys.argv
    pid, d, targets = int(args[0]), Path(args[1]), args[2:]
    c = connect()
    ok = bad = 0
    for t in targets:
        page, recs = t.split(":")
        for rec in recs.split(","):
            base = {"projectid": pid, "pageid": int(page), "recordid": int(rec)}
            try:
                z0 = tool(c, "get_zero_block", base)
                save(d / page, f"zero_{rec}_before.json", z0)
                z, note = plan(deepcopy(z0))
                if z is None:
                    print(f"SKIP {page}/{rec}: {note}")
                    continue
                z["timestamp"] = str(int(time.time() * 1000))
                save(d / page, f"zero_{rec}_NEW.json", z)
                if dry:
                    print(f"DRY {page}/{rec}: {note}")
                    continue
                tool(c, "update_zero_block", {**base, "code": z})
                back = tool(c, "get_zero_block", base)
                diff = [k for k in z if k != "timestamp" and z[k] != back.get(k)]
                if diff:
                    raise Stop(f"обратное чтение расходится в {diff[:6]}")
                ok += 1
                print(f"OK {page}/{rec}: {note}")
            except Stop as e:
                bad += 1
                print(f"STOP {page}/{rec}: {e}")
    print(f"ИТОГ: записано {ok}, ошибок {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
