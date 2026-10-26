"""Утверждение брифа человеком (Т19.6) — запрет в коде для сборки, публикации и задания писателю.

`approval.json` рядом с `brief.yaml`: кто утвердил, когда и SHA-256 брифа. Правка `brief.yaml` после утверждения
меняет сумму — утверждение снимается само: сборка и публикация получают отказ (код 1), пока человек не утвердит снова.
"""
from __future__ import annotations

import hashlib
import json

from .errors import GateRefused

BRIEF, APPROVAL = "brief.yaml", "approval.json"


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(instance, tid: str, who: str) -> dict:
    folder = instance.brief_dir(tid)
    brief = folder / BRIEF
    if not who.strip():
        raise GateRefused("утверждает человек: нужно имя (--who)")
    record = {"id": tid, "who": who.strip(), "at": instance.now().isoformat(timespec="seconds"),
              "brief_sha256": digest(brief)}
    (folder / APPROVAL).write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    back = json.loads((folder / APPROVAL).read_text(encoding="utf-8"))
    if back != record:
        raise GateRefused(f"{APPROVAL}: записано — прочитано другое")
    return record


def require(instance, tid: str) -> dict:
    folder = instance.brief_dir(tid)
    brief, mark = folder / BRIEF, folder / APPROVAL
    if not brief.is_file():
        raise GateRefused(f"тема {tid}: брифа нет — `prepare brief {tid}`, затем утверждение человеком")
    if not mark.is_file():
        raise GateRefused(f"тема {tid}: бриф не утверждён человеком — `prepare approve {tid} --who <имя>`")
    record = json.loads(mark.read_text(encoding="utf-8"))
    if record.get("brief_sha256") != digest(brief):
        raise GateRefused(f"тема {tid}: бриф изменён после утверждения ({record.get('who')}, {record.get('at')}) — "
                          "утверждение снято, нужно утвердить снова")
    return record
