-- 0013: снимок хранит число целиком (стандарт, раздел 7; ADR-0002 контура): значение, статус, пояснение, знаменатель
-- и дату съёма самого числа. Колонка as_of с этой версии — дата съёма ЧИСЛА (наименьшая из дат последних успешных
-- загрузок его систем), а не день прогона: при упавшем источнике они различаются. День прогона — taken_on.
--
-- Строки до 0013 сняты без статуса и подписаны днём прогона. Для них taken_on = as_of (это и был день прогона),
-- статус пуст: чтение возвращает такой снимок «оценкой» и говорит почему, а не выдаёт его за факт.
-- Колонки только добавляются: код до 0013 продолжает читать и писать снимки (откат выпуска не ломает базу).
ALTER TABLE facts.snapshot_number ADD COLUMN status VARCHAR;
ALTER TABLE facts.snapshot_number ADD COLUMN missing VARCHAR;
ALTER TABLE facts.snapshot_number ADD COLUMN denominator_value FLOAT8;
ALTER TABLE facts.snapshot_number ADD COLUMN taken_on DATE;
UPDATE facts.snapshot_number SET taken_on = as_of WHERE taken_on IS NULL;
