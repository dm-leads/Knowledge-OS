-- Этап 3, ревью Codex 15.09.2026 (пункты 1 и 5). Полнота загрузки визитов доказывается журналом завершённых окон id,
-- а не наличием фактов: один визит, дозапрошенный по ссылке заказа, не означает, что его окно загружено целиком.
-- Этот же журнал отвечает на вопрос «покрыт ли период»: число вне покрытия — «нет данных», а не ноль.
CREATE TABLE IF NOT EXISTS meta.window_log (
  source_system VARCHAR NOT NULL,
  window_start BIGINT NOT NULL,
  window_end BIGINT NOT NULL,          -- исключающая правая граница id
  rows_loaded INTEGER NOT NULL,
  min_started_at TIMESTAMP WITH TIME ZONE,   -- время первого и последнего визита окна: покрытие по датам
  max_started_at TIMESTAMP WITH TIME ZONE,
  load_id VARCHAR NOT NULL,
  finished_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (source_system, window_start)
);
