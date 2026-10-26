"""Адреса страниц в одном виде: хост без www в Юникоде (punycode раскрывается), путь без хвостового слеша.

Источники пишут адрес по-разному: с протоколом и без, кириллицей и punycode, путём без хоста. Сверка идёт по
нормальному виду, сверка корпуса с картой сайта — по slug без учёта раздела.
"""
from __future__ import annotations

from urllib.parse import unquote, urlsplit


def host_unicode(host: str) -> str:
    host = host.lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = []
    for label in host.split("."):
        if label.startswith("xn--"):
            try:
                label = label.encode("ascii").decode("idna")
            except UnicodeError:
                pass
        parts.append(label)
    return ".".join(parts)


def norm_url(url: str, default_host: str = "") -> str:
    """«хост/путь» без протокола, www, параметров и хвостового слеша."""
    url = (url or "").strip().replace("\\/", "/")
    if not url:
        return ""
    if "://" not in url:
        url = ("//" + url) if not url.startswith("/") else ("//" + default_host + url)
    parts = urlsplit(url)
    path = unquote(parts.path).rstrip("/")
    return host_unicode(parts.netloc) + path


def host_of(url: str) -> str:
    return norm_url(url).split("/", 1)[0]


def path_parts(url: str) -> list[str]:
    normal = norm_url(url)
    return [p for p in normal.split("/")[1:] if p]


def slug_of(url: str) -> str:
    parts = path_parts(url)
    return parts[-1] if parts else ""


def section_of(url: str) -> str:
    parts = path_parts(url)
    return parts[0] if len(parts) > 1 else ""


def is_own(url: str, domains) -> bool:
    host = host_of(url)
    own = {host_unicode(d) for d in domains}
    return host in own or any(host.endswith("." + d) for d in own)


def ascii_url(url: str) -> str:
    """Адрес с хостом в punycode — так его ждут консоли и IndexNow (кириллический домен)."""
    normal = norm_url(url)
    host, _, path = normal.partition("/")
    return "https://" + host.encode("idna").decode("ascii") + ("/" + path if path else "")
