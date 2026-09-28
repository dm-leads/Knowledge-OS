---
name: image-edit
description: >
  Use to edit a RASTER image (JPG/PNG) seamlessly — change text or numbers inside a graphic/banner,
  remove or add an object, restyle, fix a detail — where manual pixel-patching (Pillow crop/fill) would
  leave a visible patch. Triggers on "поменяй цифру/текст на картинке/баннере", "убери/добавь объект на
  фото", "отредактируй изображение", "замени X на Y на картинке", "edit this image/banner", "change the
  number in this image", "seamless image edit". Uses Google Gemini image model (Nano Banana) via
  GOOGLE_AI_STUDIO_API_KEY — it truly redraws the background instead of pasting a patch. Do NOT use for
  pixel-perfect needs when the original design source (Figma/Canva/PSD) exists — edit the source instead.
---

# image-edit — бесшовное редактирование картинок через Gemini

Для правок растровых изображений (поменять цифру/текст в плашке, убрать/добавить объект, поправить деталь)
**используй генеративный редактор Gemini**, а НЕ ручную заплатку в Pillow. Ручной путь (вырезать текст →
залить градиент → нарисовать новый) оставляет видимый шов и выглядит плохо — проверено, тупик.

## Когда что

- **Есть исходник макета** (Figma/Canva/PSD) → правь ИСХОДНИК, это пиксель-идеально. Спроси у Дмитрия.
- **Только готовый JPG/PNG** → Gemini image edit (ниже). Бесшовно.

## Перед запуском (обязательно)

- Это **платный внешний вызов** (~$0.04/картинку) и **картинка уходит в Google** → сказать стоимость и получить «да» (особенно для чувствительного/ПДн — тогда нельзя).
- Это **полный ре-рендер**: модель перерисовывает всю картинку, не вырезает кусочек. После — **сверить**, не «поплыло» ли что-то ещё; и **размер может измениться** (модель отдаёт крупнее / чуть иную пропорцию) — при необходимости подогнать под исходные размеры.
- Ключ: `GOOGLE_AI_STUDIO_API_KEY` из `C:\Users\redmi\Second Brain Secrets\.env` (в чат не печатать). Предпочитать его, не OpenRouter (личные деньги).

## Рабочий рецепт (проверен 2026-08-12)

Модель `gemini-2.5-flash-image`, endpoint `:generateContent`. Промпт: точечно опиши что менять и добавь
«keep everything else identical, same font/size/position, same resolution/aspect». Явно перечисли, что НЕ трогать.

```python
import base64, json, urllib.request, re
env=r"C:\Users\redmi\Second Brain Secrets\.env"; key=None
for line in open(env,encoding='utf-8',errors='ignore'):
    m=re.match(r'\s*GOOGLE_AI_STUDIO_API_KEY\s*=\s*(.+)\s*$',line)
    if m: key=m.group(1).strip().strip('"').strip("'"); break
b64=base64.b64encode(open(SRC,'rb').read()).decode()
prompt=("Edit this image. <ЧТО ИМЕННО поменять и где>. Keep the exact same font, weight, color, size, "
"position, background/gradient, and absolutely everything else identical. Do NOT change <ЧТО НЕ ТРОГАТЬ>. "
"Return the full edited image at the same resolution and aspect ratio.")
payload={"contents":[{"parts":[{"inline_data":{"mime_type":"image/jpeg","data":b64}},{"text":prompt}]}],
         "generationConfig":{"responseModalities":["IMAGE"]}}
url=f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={key}"
req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
resp=json.load(urllib.request.urlopen(req,timeout=180))
for p in resp.get('candidates',[{}])[0].get('content',{}).get('parts',[]):
    d=p.get('inlineData') or p.get('inline_data')
    if d and d.get('data'): open(OUT,'wb').write(base64.b64decode(d['data']))
```

- На ошибку `urllib.error.HTTPError` — распечатать `e.code` и тело (без ключа) для диагностики.
- Несколько картинок — делать по одной, первую проверить глазами, потом остальные.
- Сохранять в НОВЫЙ файл (`*__ai.jpg`), оригинал не перезаписывать без спроса.

## После

- Показать результат, сверить с оригиналом (текст/детали не «поплыли»), сообщить изменение размера.
- Предложить: подгонка под точные исходные размеры / оставить в высоком разрешении / перезаписать оригинал.
