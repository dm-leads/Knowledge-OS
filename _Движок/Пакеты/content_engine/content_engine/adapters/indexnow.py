"""IndexNow — Яндекс и Bing (Т24.1). Запись вовне: заявка о новом адресе.

Ключ — по имени переменной (`indexing.indexnow.key_env`); он публичный: по устройству протокола лежит на сайте открытым
файлом. До отправки GET-ом проверяется файл ключа (`key_location` или `https://<домен>/<ключ>.txt`): 200 и
совпадение, иначе — «не подключено» с заданием положить ключ. Хост в заявке — в punycode. Подтверждение — код ответа
200 или 202 (Т16.3).
"""
from __future__ import annotations

from ..core.actions import Action
from ..core.urls import ascii_url
from .write_client import WriteClient

ENDPOINTS = {"yandex": ("https://yandex.com", "yandex.com"), "bing": ("https://www.bing.com", "www.bing.com")}


def key_location(instance, key: str) -> str:
    own = instance.p["indexing"]["indexnow"]["key_location"]
    return own or f"https://{instance.p['site']['domains'][0]}/{key}.txt"


def key_file_ok(instance, key: str, *, transport=None) -> tuple[bool, str]:
    from ..core.errors import SourceError
    from .page_fetch import PageFetcher
    location = key_location(instance, key)
    try:
        got = PageFetcher.for_own_site(instance, transport=transport).get(location, allow_redirects=False)
    except SourceError as exc:
        return False, f"файл ключа {location}: {exc}"
    if got.status != 200:
        return False, f"файл ключа {location}: HTTP {got.status} — положите файл с ключом на сайт"
    if got.text.strip() != key:
        return False, f"файл ключа {location}: содержимое не совпадает с ключом"
    return True, f"файл ключа {location} на месте"


def submit_actions(instance, url: str, key: str, *, transport=None) -> list[Action]:
    full = ascii_url(url)
    host = full.split("/")[2]
    payload = {"host": host, "key": key, "keyLocation": ascii_url(key_location(instance, key)), "urlList": [full]}
    out = []
    for system, (base, api_host) in ENDPOINTS.items():
        client = WriteClient(f"IndexNow {system}", base, {("POST", "indexnow")}, {"Content-Type": "application/json"},
                             hosts=[api_host], transport=transport if transport is not None else instance.transport)

        def do(client=client):
            return client.call("POST", "indexnow", json=payload)

        def verify(answer):
            ok = answer.status in (200, 202)
            return ok, f"HTTP {answer.status}"

        out.append(Action(f"indexnow_{system}", f"{base}/indexnow", "POST", f"заявка о {full}", "status_code",
                          ("indexing", "write"), do=do, verify=verify, needs_snapshot=False,
                          meta={"system": f"indexnow_{system}", "url": url, "independent": True}))
    return out
