"""Адаптер речевой аналитики (класс F): сколько фактов каждого значения извлечено из звонков и переписок.

Цифры говорят «где», диалоги — «почему». Адаптер отдаёт частоты значений факта (например, причин отказа), базу без
пустышек «нет» — для долей по канону, номера фактов одного значения и фрагменты-доказательства по номерам. SQL строит
только адаптер: транзакция только на чтение (страж 11), без колонок с ПДн и сырой цитаты (страж 12), код факта — из
белого списка конфигурации. Доказательства из фрагментов собирает движок цикла (страж 10).
"""
from __future__ import annotations

import re
import subprocess
from datetime import date

from ..core.config import InstanceConfig
from ..core.cycle import Fragment, Unavailable
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, SourceError

SYSTEM = "speech_analytics"
TABLE = "extracted_parameters"
BREAKDOWN = "value"
EMPTY = "(нет)"
MAX_FACT_IDS = 100
_CODE = re.compile(r"^[a-z0-9_]+$")
_COLUMN = re.compile(r"[a-z_][a-z0-9_]*")
_FACT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|grant)\b", re.IGNORECASE)
_LITERAL = re.compile(r"'(?:[^']|'')*'")
# Чтение строки целиком обошло бы запрет колонок: «*», «таблица.*», строка в json или массив, составное значение, выбор
# алиаса таблицы адаптера целиком или его приведение к тексту. Запросы адаптера строятся только внутри него.
_WHOLE_ROW = re.compile(r"\*\s*from\b|\.\*"
                        r"|\b(?:to_jsonb?|row_to_json|jsonb?_build_object|jsonb?_agg|array_agg|row)\s*\("
                        r"|(?:\bselect|,|\()\s*(?:ep|parameter|link|conversation|other)\s*(?:,|::|\)|\bfrom\b)",
                        re.IGNORECASE)
_TRIMMED_VALUE = r"regexp_replace(ep.value #>> '{}', '^\s+|\s+$', '', 'g')"
CHANNELS = {
    "calls": ("join call_project_links link on link.id = ep.call_project_link_id "
              "join audio_recordings conversation on conversation.id = link.audio_recording_id",
              "conversation.call_date"),
    "chats": ("join chat_project_links link on link.id = ep.chat_project_link_id "
              "join chat_conversations conversation on conversation.talk_id = link.talk_id",
              "conversation.first_message_at"),
}
CONVERSATION = {"calls": "conversation.id::text", "chats": "conversation.talk_id::text"}
CHAT_WARNING = ("факты переписок до 09.09.2026 извлекались по всему chat_id — цитата может лежать в соседней "
                "беседе; на частоты не влияет")
# 09.09.2026 извлечение переписок переведено с chat_id на беседу; время правки неизвестно, поэтому помечаются факты
# по 09.09 включительно и факты без даты извлечения. Прежние факты не переизвлекались.
CHAT_BINDING_FIXED = date(2026, 9, 10)
CHAT_BINDING_NOTE = ("факт извлечён не позже 09.09.2026 из чата с несколькими беседами — цитата может лежать в "
                     "соседней беседе того же chat_id")


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _flat(expression: str) -> str:
    """Текст одной строкой: перевод строки внутри значения разорвал бы построчный ответ psql."""
    return f"regexp_replace(coalesce({expression}, ''), '\\s+', ' ', 'g')"


class SshPsqlRunner:
    """Выполняет SQL в базе на сервере через ssh и docker exec psql; SQL подаётся через stdin."""

    def __init__(self, section: dict, secrets: dict):
        host, port, user, key = (secrets[name] for name in section["secret_env"])
        remote = (f"docker exec -i {section['container']} psql -U {section['db_user']} -d {section['database']} "
                  "-At -F '\x1f' -v ON_ERROR_STOP=1")
        self.command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-i", key, "-p", str(port),
                        f"{user}@{host}", remote]

    def __call__(self, sql: str) -> list[tuple[str, ...]]:
        result = subprocess.run(self.command, input=sql, capture_output=True, text=True, encoding="utf-8",
                                timeout=300)
        if result.returncode != 0:
            raise SourceError(f"psql: код возврата {result.returncode}")
        rows = []
        for line in result.stdout.splitlines():
            if line and line not in ("BEGIN", "COMMIT"):
                rows.append(tuple(line.split("\x1f")))
        return rows


class SpeechAnalyticsAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, secrets: dict | None = None, runner=None):
        self.section, self.cfg = section, cfg
        self.runner = runner if runner is not None else SshPsqlRunner(section, secrets)

    def _run(self, body: str) -> list[tuple[str, ...]]:
        """Страж 11: шаблон запроса без подставленных значений проверяется на запись, запрещённые колонки (по целому
        имени: сырая цитата запрещена, очищенная рядом с ней — нет) и чтение строки целиком."""
        template = _LITERAL.sub("''", body).lower()
        forbidden = any(re.search(rf"\b{re.escape(column.lower())}\b", template)
                        for column in self.section["forbidden_columns"])
        if _WRITE_WORDS.search(template) or forbidden or _WHOLE_ROW.search(template):
            raise GuardViolation(11, "запрос к базе диалогов нарушает правило «только чтение, без ПДн» — не отправлен")
        return self.runner(f"begin transaction read only;\n{body}\ncommit;\n")

    def _code(self, metric: str) -> str:
        code = metric.removeprefix("facts_")
        if not metric.startswith("facts_") or not _CODE.match(code) or code not in self.section["fact_codes"]:
            raise GuardViolation(9, f"метрика «{metric}» не описана для базы диалогов в конфигурации")
        return code

    def _base(self, query: Query) -> tuple[str, str, str]:
        """Проверки запроса и общее «from … where»: код факта из белого списка, канал, период по дате беседы."""
        if query.flow != "all":
            raise GuardViolation(9, "поток по визиту в диалогах не определяется — только all")
        if query.scope not in CHANNELS or query.scope not in self.section["scopes"]:
            raise GuardViolation(9, f"канал «{query.scope}» не объявлен: calls или chats")
        code = self._code(query.metric)
        joins, date_column = CHANNELS[query.scope]
        where = (f"where parameter.code = {_literal(code)} "
                 f"and {date_column} >= {_literal(query.period_start.isoformat())} "
                 f"and {date_column} < {_literal(query.period_end.isoformat())}")
        return code, date_column, (f"from {TABLE} ep join analysis_parameters parameter on parameter.id = "
                                   f"ep.parameter_id {joins} {where}")

    def fetch(self, query: Query) -> list[Number]:
        if query.breakdown not in (None, BREAKDOWN):
            raise NotImplementedError(f"разрез «{query.breakdown}» пока не поддержан — только {BREAKDOWN}")
        code, _, base_from = self._base(query)
        level = self.cfg.rule(query.metric).level
        empties = ", ".join(_literal(value) for value in self.section["empty_values"])
        value_text = "lower(trim(ep.value #>> '{}'))"
        notes = [CHAT_WARNING] if query.scope == "chats" else []
        common = dict(metric=query.metric, level=level, scope=query.scope, flow="all",
                      period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      source=f"F:{SYSTEM}:{TABLE}")
        if query.breakdown is None:
            rows = self._run(f"select count(*) {base_from} and coalesce({value_text}, '') not in ({empties});")
            missing = "; ".join(["база без пустышек «нет» — знаменатель долей"] + notes)
            return [Number(**common, status=Status.FACT, value=float(rows[0][0]) if rows else 0.0, missing=missing)]
        rows = self._run(f"select case when coalesce({value_text}, '') in ({empties}) then {_literal(EMPTY)} "
                         f"else ep.value #>> '{{}}' end as value, count(*) {base_from} group by 1;")
        return [Number(**common, segment=f"{code}={value}", status=Status.FACT, value=float(count),
                       missing="; ".join(notes))
                for value, count in sorted(rows, key=lambda row: row[0]) if value]

    def fact_ids(self, query: Query, value: str, limit: int = 5) -> list[str]:
        """Номера фактов одного значения за период, свежие беседы первыми, — путь от частоты к доказательствам."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_FACT_IDS:
            raise GuardViolation(9, f"число номеров {limit!r} — нужно целое от 1 до {MAX_FACT_IDS}")
        empties = {item.strip().lower() for item in self.section["empty_values"]} | {EMPTY}
        if not isinstance(value, str) or value.strip().lower() in empties:
            raise GuardViolation(9, f"значение «{value}» — пустышка, у неё нет доказательств")
        _, date_column, base_from = self._base(query)
        rows = self._run(f"select ep.id::text {base_from} and {_TRIMMED_VALUE} = {_literal(value.strip())} "
                         f"order by {date_column} desc, ep.id limit {limit};")
        return [row[0] for row in rows]

    def fragments(self, ids) -> list[Fragment | Unavailable]:
        """Фрагменты-доказательства по номерам фактов: канал, беседа, дата, код, значение и очищенная цитата.
        Сырая цитата и колонки с ПДн не читаются; факты вне белого списка кодов не отдаются. Пустая дата беседы —
        фрагмент без даты, его вычёркивает движок цикла. Номер, который база знает, но не отдаёт фрагментом, возвращается
        как недоступный с причиной: код вне белого списка или нет связи с беседой."""
        ids = list(ids)
        if any(not isinstance(x, str) or not _FACT_ID.fullmatch(x) for x in ids):
            raise GuardViolation(10, "номера фактов не в формате uuid — запрос не отправлен")
        if not ids:
            return []
        quote = self.section["quote_column"]
        if not _COLUMN.fullmatch(quote) or quote in self.section["forbidden_columns"]:
            raise GuardViolation(12, f"колонка цитаты «{quote}» запрещена или записана не как имя колонки")
        numbers = ", ".join(_literal(x) for x in ids)
        codes = ", ".join(_literal(code) for code in self.section["fact_codes"])
        value_column, quote_column = _flat("ep.value #>> '{}'"), _flat(f"ep.{quote}")
        parts = []
        for channel in (name for name in CHANNELS if name in self.section["scopes"]):
            joins, date_column = CHANNELS[channel]
            binding = "false" if channel == "calls" else (
                f"(coalesce(ep.extracted_at < {_literal(CHAT_BINDING_FIXED.isoformat())}, true) and (select count(*) "
                "from chat_conversations other where other.chat_id = conversation.chat_id) > 1)")
            parts.append(f"select ep.id::text, {_literal(channel)}, {CONVERSATION[channel]}, {date_column}::date, "
                         f"parameter.code, {value_column}, {quote_column}, {binding} "
                         f"from {TABLE} ep join analysis_parameters parameter on parameter.id = ep.parameter_id "
                         f"{joins} where ep.id in ({numbers}) and parameter.code in ({codes})")
        rows = self._run("\nunion all\n".join(parts) + ";")
        found = [Fragment(id=row[0], channel=row[1], conversation=row[2],
                          on=date.fromisoformat(row[3]) if row[3] else None, code=row[4], value=row[5], quote=row[6],
                          note=CHAT_BINDING_NOTE if row[7] == "t" else "")
                 for row in rows]
        returned = {fragment.id for fragment in found}
        missing = [x for x in ids if x not in returned]
        if not missing:
            return found
        allowed = set(self.section["fact_codes"])
        known = self._run(f"select ep.id::text, coalesce(parameter.code, '') from {TABLE} ep left join "
                          f"analysis_parameters parameter on parameter.id = ep.parameter_id "
                          f"where ep.id in ({', '.join(_literal(x) for x in missing)});")
        return found + [Unavailable(id=row[0], reason=f"код факта «{row[1]}» вне белого списка конфигурации"
                                    if row[1] not in allowed else "нет связи с беседой в базе диалогов")
                        for row in known]

    def probe(self) -> list[ProbeResult]:
        try:
            rows = self._run("select count(*) from chat_messages;")
        except (SourceError, OSError, subprocess.TimeoutExpired) as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        return [ProbeResult(SYSTEM, True, None, f"строк сообщений: {rows[0][0] if rows else 'нет ответа'}")]
