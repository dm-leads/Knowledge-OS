"""Ошибка правила Ядра данных: код красного случая по канону (К1–К10) и тяжесть для гейта."""

RULE_CODES = tuple(f"К{i}" for i in range(1, 11))


class RuleViolation(Exception):
    STRUCTURE = "структура"   # 🟥 число будет ложным
    COVERAGE = "покрытие"     # 🟧 источник не отвечает, день не загружен

    def __init__(self, code: str, message: str, severity: str = STRUCTURE):
        if code not in RULE_CODES:
            raise ValueError(f"кода правила «{code}» нет в каноне: {RULE_CODES}")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.severity = severity
