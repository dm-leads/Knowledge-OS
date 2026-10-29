"""Ошибки машины. Каждая называет, что не так и где, — без значений секретов."""


class EngineError(RuntimeError):
    """Общая ошибка машины: команда останавливается с кодом 3."""


class ProfileError(EngineError):
    """Профиль проекта не прошёл проверку: в сообщении — имя поля."""


class StoreError(EngineError):
    """Реестр не записался или не читается: чужая версия схемы, сверка «записано — прочитано» не сошлась."""


class SourceError(EngineError):
    """Внешний источник ответил ошибкой — это не «данных нет»."""


class AccessDenied(SourceError):
    """HTTP 401/403: доступ отозван или ключ сменили."""


class NotFound(SourceError):
    """HTTP 404: источник не нашёл объект запроса (у Keys.so check-top — ни одной фразы пачки нет в базе)."""


class NetworkUnreachable(SourceError):
    """Сеть не ответила (тайм-аут, обрыв соединения): это «не проверено», а не ответ сайта (О1 волны 3)."""


class EndpointNotAllowed(SourceError):
    """Адрес вне белого списка адаптера: запрос не отправлен."""


class QuotaExhausted(SourceError):
    """Суточная квота источника выбрана: повторять в цикле нельзя, только после обновления лимита."""


class ReportPending(SourceError):
    """Отчёт сервиса строится (HTTP 202) дольше допустимого ожидания: не сбой, а «повторить позже»."""


class GateRefused(EngineError):
    """Запрет в коде: тему нельзя поставить в работу без пройденного гейта."""


class RightsRefused(GateRefused):
    """Ступень прав профиля ниже нужной: действие не входит в план и не выполняется (Т16.1)."""


class LockBusy(GateRefused):
    """Живая блокировка темы и действия: второй прогон не идёт (Т16.4)."""


class SnapshotMissing(GateRefused):
    """Нет снимка «до»: запись в живую страницу, CMS или базу проекта не выполняется (Т16.5)."""


class BudgetRefused(EngineError):
    """Бюджет-гейт: платный вызов без «да» человека, выше потолка или без человека — ноль вызовов (Т17.2)."""


class WriteUnconfirmed(EngineError):
    """Запись ушла, но подтверждение (чтение обратно, код ответа, заявка) не получено (Т16.3)."""


def source_failures() -> tuple:
    """Сбой внешнего источника любого рода: ошибка машины, сети, `requests`, авторизации Google. Блок с таким сбоем
    пишет «источник не ответил», а не роняет всю команду (упавший источник не мешает остальным блокам, Т34.4)."""
    import requests
    out = [SourceError, OSError, ValueError, requests.exceptions.RequestException]
    try:
        from google.auth.exceptions import GoogleAuthError
        out.append(GoogleAuthError)
    except ImportError:
        pass
    return tuple(out)
