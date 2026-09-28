-- 0002: время последнего изменения сделки в источнике — курсор инкремента и сравнение съёмов.
ALTER TABLE facts.deal ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE;
