import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.pii import (find_pii, hash_email, hash_phone, normalize_email, normalize_phone,
                                 require_no_pii)

KEY = b"test-instance-key"


def test_phone_formats_normalize_to_one_value():
    assert normalize_phone("+7 (999) 000-00-00") == "79990000000"
    assert normalize_phone("8-999-000-00-00") == "79990000000"
    assert normalize_phone("") is None and normalize_phone(None) is None


def test_same_phone_same_hash_different_key_different_hash():
    a, b = hash_phone("+7 999 000 00 00", KEY), hash_phone("89990000000", KEY)
    assert a == b and len(a) == 64
    assert hash_phone("89990000000", b"other") != a
    assert hash_phone(None, KEY) is None


def test_email_normalizes_and_hashes():
    assert normalize_email("  Ivan@Example.COM ") == "ivan@example.com"
    assert hash_email("Ivan@Example.COM", KEY) == hash_email("ivan@example.com", KEY)


def test_find_pii_detects_phone_and_email_but_not_ids():
    assert find_pii("звонил с +7 999 000-00-00") == "телефон"
    assert find_pii("пишите на ivan@example.com") == "почта"
    assert find_pii("сделка 12345678, визит 9911223344") is None      # ID не телефон: нет кода страны


def test_require_no_pii_raises_k6_and_skips_hash_columns():
    rows = [{"note": "тел 89990000000", "phone_hash": "abc"}]
    with pytest.raises(RuleViolation) as e:
        require_no_pii(rows)
    assert e.value.code == "К6" and "note" in str(e.value)
    require_no_pii([{"phone_hash": "89990000000"}])                          # хэш-колонка не проверяется


def test_company_line_allowed_only_from_whitelist():
    allowed = {"callee_line": frozenset({"74950000121"})}
    require_no_pii([{"callee_line": "+7 495 000 01 21"}], allowed=allowed)     # номер линии компании — не ПДн
    with pytest.raises(RuleViolation) as e:
        require_no_pii([{"callee_line": "+7 999 000 00 00"}], allowed=allowed) # чужой номер в той же колонке — К6
    assert e.value.code == "К6"


def test_long_free_text_is_rejected_as_k6():
    with pytest.raises(RuleViolation) as e:
        require_no_pii([{"declared_source": "к" * 201}])
    assert e.value.code == "К6" and "свободный текст" in str(e.value)


def test_company_phone_inside_text_allowed_foreign_rejected():
    allowed = {"entry_channel_tech": frozenset({"79990000410"})}
    require_no_pii([{"entry_channel_tech": "[WA]79990000410"}], allowed=allowed)    # линия компании внутри текста
    require_no_pii([{"entry_channel_tech": "calltracker"}], allowed=allowed)             # текст без телефона
    with pytest.raises(RuleViolation) as e:
        require_no_pii([{"entry_channel_tech": "[WA] 79990000000"}], allowed=allowed)
    assert e.value.code == "К6"


def test_exact_phone_column_and_email_everywhere_after_review():
    allowed = {"callee_line": frozenset({"74950000121"}), "entry_channel_tech": frozenset({"79990000410"})}
    with pytest.raises(RuleViolation) as e:
        require_no_pii([{"callee_line": "calltracker"}], allowed=allowed)            # callee_line — только номер линии целиком
    assert e.value.code == "К6"
    with pytest.raises(RuleViolation) as e:
        require_no_pii([{"entry_channel_tech": "ivan@example.com"}], allowed=allowed)   # почта запрещена и в разрешённых колонках
    assert e.value.code == "К6" and "почта" in str(e.value)


def test_opaque_id_columns_are_not_scanned_for_phones():
    """Идентификатор документа источника — не текст и телефона содержать не может. Живой случай 16.09.2026:
    несколько номеров платежей учётной системы совпали с шаблоном телефона (например «f8912345-6789-11f0-...»
    даёт «8912345-6789» — телефон 8-912-345-67-89) и остановили загрузку по К6.

    Ослаблять правило нельзя, поэтому колонка объявляется технической: в ней разрешён только код — буквы,
    цифры, дефис, двоеточие, подчёркивание — и ничего похожего на текст."""
    from datacore.schema.pii import require_no_pii
    rows = [{"payment_id": "payment_in:f8912345-6789-11f0-0a80-000000000001"},
            {"payment_id": "refund:c7012345-6789-11f1-0a80-000000000002"}]
    require_no_pii(rows)                       # не должно поднимать К6


def test_opaque_id_column_still_rejects_real_text():
    """Техническая колонка — не лазейка: пробелы и кириллица в ней запрещены, иначе туда однажды положат имя."""
    from datacore.schema.errors import RuleViolation
    from datacore.schema.pii import require_no_pii
    with pytest.raises(RuleViolation) as e:
        require_no_pii([{"payment_id": "Иванов Иван 8 916 123-45-67"}])
    assert e.value.code == "К6"
    with pytest.raises(RuleViolation) as e:
        require_no_pii([{"payment_id": "8 916 123-45-67"}])
    assert e.value.code == "К6"


@pytest.mark.parametrize("column", ["shipment_id", "invoice_id"])
def test_cogs_document_ids_are_opaque_too(column):
    """Этап 5 добавил ещё два кода документов — отгрузку и счёт, — но в список технических колонок их не внёс.
    Живой случай 23.09.2026: первая загрузка на сервере за девять месяцев остановилась по К6 на строке 1343
    колонки «shipment_id». На машине разработчика грузился только август, там совпадения не нашлось."""
    from datacore.schema.errors import RuleViolation
    from datacore.schema.pii import require_no_pii
    require_no_pii([{column: "f8912345-6789-11f0-0a80-000000000001"}])      # код — проходит
    with pytest.raises(RuleViolation):
        require_no_pii([{column: "Иванов Иван 8 916 123-45-67"}])           # текст — по-прежнему нет


def test_every_text_document_id_column_is_declared_opaque():
    """Страж на будущее: колонку с кодом документа находил не тест, а живая загрузка — дважды (16.09 и 23.09.2026).
    Теперь любая текстовая колонка «…_id» в фактах обязана быть объявлена технической. load_id составляет само
    ядро («система-дата-время»), телефону там взяться неоткуда."""
    from datacore.schema.engine import connect
    from datacore.schema.migrate import migrate
    from datacore.schema.pii import OPAQUE_ID_COLUMNS
    with connect("duckdb:///:memory:") as e:
        migrate(e)
        rows = e.fetchall("SELECT table_name, column_name FROM information_schema.columns "
                          "WHERE table_schema = 'facts' AND column_name LIKE '%id' AND data_type = 'VARCHAR'")
    missing = sorted(f"{t}.{c}" for t, c in rows if c != "load_id" and c not in OPAQUE_ID_COLUMNS)
    assert missing == [], f"текстовые коды документов без отметки OPAQUE_ID_COLUMNS: {missing}"
