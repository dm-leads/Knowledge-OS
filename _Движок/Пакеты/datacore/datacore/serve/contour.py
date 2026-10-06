"""Набор отбора сделок → условие SQL (этап 5, требование Я12).

Метрика в ядре одна, а отбор — её параметр: «сколько сделок у компании», «сколько новых первых квалифицированных»
и «сколько их же по правилам digital-контура» считает одна модель с разными наборами. Сами наборы лежат в
конфигурации инстанса, потому что это договорённость компании, а не свойство данных: правила менялись и будут
меняться.

Набор состоит из базы (какие сделки вообще берём) и списка исключений. База — тоже отбор, и она обязана быть
здесь, а не внутри метрики: иначе вопрос «сколько сделок у компании» остаётся без ответа, как было до 17.09.2026
(у первого инстанса метрика видела лишь меньшую часть сделок месяца, а среди скрытых были сделки с оплатами).

Поля берутся из фактов, имён поставщика здесь нет — граница К9.

`keep_referrer` — исключение из исключения: площадка перехода, которая возвращает лид в зачёт, даже если его
группа источника исключена. Нужно, потому что поставщик сваливает в одну группу и наш контент на внешних
площадках, и технические переходы служебных систем. Ядро может разобрать её по площадке; витрина
поставщика — нет, там фильтр
по полю сделки режет заодно и визиты."""
from __future__ import annotations

from datacore.schema.errors import RuleViolation

# Куда смотрит каждое поле набора: имя в конфигурации → выражение SQL. Ничего, кроме этих трёх, не разрешено —
# иначе набор отбора стал бы способом писать произвольный SQL из конфигурации.
FIELD_SQL = {"marker_level_1": "m.marker_level_1",
             "entry_channel_tech": "d.entry_channel_tech",
             "entry_channel_summary": "d.entry_channel_summary"}

# Площадка перехода живёт в фактах визитов; метрика присоединяет их, когда набор этого требует.
REFERRER_SQL = "v.referrer_host"

# «Одна сделка на контакт»: у контакта засчитывается самая ранняя из его первых квалифицированных сделок.
# Флаг ставит CRM, и он не всегда единственный у контакта (у первого инстанса — 6 контактов с двумя флагами
# за 2026 год). Удалённая и исключённая как технический тест сделка место первой не занимает. При равном
# времени создания порядок задаёт номер сделки — иначе обе сделки выпали бы или обе остались.
FIRST_PER_CONTACT_SQL = (
    "NOT EXISTS (SELECT 1 FROM facts.deal p WHERE p.contact_id = d.contact_id AND p.is_new_first "
    "AND p.deleted_at IS NULL "
    "AND (p.created_at < d.created_at OR (p.created_at = d.created_at AND p.deal_id < d.deal_id)) "
    "AND NOT EXISTS (SELECT 1 FROM facts.exclusion pe WHERE pe.entity = 'deal' "
    "AND pe.entity_key = CAST(p.deal_id AS VARCHAR)))")


def contour_names(cfg) -> tuple[str, ...]:
    return tuple(cfg.contours or {})


def _contour(cfg, name: str) -> dict:
    contours = cfg.contours or {}
    if name not in contours:
        raise RuleViolation("К2", f"набор отбора «{name}» не объявлен в конфигурации: {sorted(contours)}")
    return contours[name] or {}


def contour_base(cfg, name: str) -> dict:
    """База набора: какие сделки вообще берём. Метрика называет её в пояснении к числу."""
    return dict(_contour(cfg, name).get("base") or {})


def contour_needs_visit(cfg, name: str) -> bool:
    """Нужны ли набору факты визитов: правило по площадке перехода есть только там."""
    return any(rule.get("keep_referrer") for rule in (_contour(cfg, name).get("exclude") or []))


def contour_clause(cfg, name: str) -> tuple[str, list]:
    """Условие для WHERE и его параметры. Пустая строка — набор не ограничивает ничего."""
    contour = _contour(cfg, name)
    parts, params = [], []
    base = contour.get("base") or {}
    if base.get("new_first_only"):
        parts.append("d.is_new_first")
    if base.get("first_per_contact"):
        if not base.get("new_first_only"):
            raise RuleViolation("К2", f"набор «{name}»: «first_per_contact» имеет смысл только вместе с "
                                      "«new_first_only» — первая сделка выбирается среди первых квалифицированных")
        parts.append(FIRST_PER_CONTACT_SQL)
    for rule in contour.get("exclude") or []:
        field = rule.get("field")
        if field not in FIELD_SQL:
            raise RuleViolation("К2", f"набор «{name}»: поле «{field}» не разрешено, только {sorted(FIELD_SQL)}")
        column = FIELD_SQL[field]
        values = [str(v) for v in rule.get("exclude") or []]
        keep = [str(v) for v in rule.get("keep_referrer") or []]
        if values:
            marks = ", ".join("?" for _ in values)
            # COALESCE: пустое значение поля — это «неизвестно», а не «запрещено». Без него сделки без следа
            # (несколько процентов сделок месяца) выпали бы из всех наборов, и разница между ними перестала бы быть объяснимой.
            condition = f"COALESCE({column}, '') NOT IN ({marks})"
            if keep:
                # Исключение из исключения: лид с этой площадки остаётся в зачёте, хотя его группа исключена.
                # Пример: группа «переходы с сайтов» содержит и наш контент на внешней площадке,
                # и служебный переход внутри CRM.
                keep_marks = ", ".join("?" for _ in keep)
                condition = f"({condition} OR COALESCE({REFERRER_SQL}, '') IN ({keep_marks}))"
                parts.append(condition)
                params.extend(values)
                params.extend(keep)
            else:
                parts.append(condition)
                params.extend(values)
        # Сравнение по началу строки: источник обрезает длинные значения. Канал «Рекомендация (сосед, знакомый,
        # プロ…)» лежит в фактах как «Рекомендация (сосед, знакомый, про», и точное сравнение его не находило —
        # набор отбора пропускал такую сделку (живая проверка 17.09.2026).
        prefixes = [str(v) for v in rule.get("exclude_prefix") or []]
        for prefix in prefixes:
            parts.append(f"COALESCE({column}, '') NOT LIKE ?")
            params.append(prefix + "%")
    return " AND ".join(parts), params
