"""Обезличивание текста до записи и до модели: почты, ссылки на мессенджеры, @ники и телефоны — пометками.
Имя, ник и номер автора сообщения код не запрашивает вовсе."""
from __future__ import annotations

import re

PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s\-()]*){10,15}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
NICK = re.compile(r"(?<!\w)@\w{4,}")
LINK = re.compile(r"(?i)(?:tg://\S+|(?:https?://)?(?:www\.|m\.)?(?:t\.me|telegram\.me|wa\.me|vk\.com|vk\.ru|"
                  r"instagram\.com|facebook\.com|fb\.com|ok\.ru|x\.com|twitter\.com|tiktok\.com|youtube\.com|"
                  r"max\.ru|dzen\.ru)/\S+)")
FLAT = re.compile(r"(?i)\b(?:кв\.?|квартир[аеуы])\s*№?\s*\d{1,4}\b")


def clean(text: str) -> str:
    text = EMAIL.sub("[почта]", text)
    text = LINK.sub("[ссылка]", text)
    text = NICK.sub("[ник]", text)
    text = PHONE.sub("[телефон]", text)
    text = FLAT.sub("[квартира]", text)
    return re.sub(r"\s+", " ", text).strip()
