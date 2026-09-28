"""Ошибка стража Движка роста: номер стража по канону и тяжесть для гейта."""


class GuardViolation(Exception):
    STRUCTURE = "структура"   # 🟥 вывод будет ложным
    COVERAGE = "покрытие"     # 🟧 источник не отвечает, артефакт не заполнен

    def __init__(self, guard: int, message: str, severity: str = STRUCTURE):
        super().__init__(f"[страж {guard}] {message}")
        self.guard = guard
        self.severity = severity
