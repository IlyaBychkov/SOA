# HW5 — Online Cinema

Movie Service → Kafka `movie-events` → ClickHouse Kafka Engine → Materialized View → таблица `movie_events`.

Ключ Kafka — `user_id`. События одного пользователя идут в одну партицию и сохраняют порядок записи.

Команды выполняются из папки `HW5`.

## Запуск

```bash
GENERATE_EVENTS=1 docker compose up --build -d --wait
```

Генератор отправляет по одному событию каждые 3 секунды.

## Swagger

Открой [Swagger](http://localhost:8000/docs), выбери `POST /events`, нажми **Try it out**, вставь JSON и нажми **Execute**.

```json
{
  "user_id": "swagger-demo",
  "movie_id": "movie-1",
  "event_type": "VIEW_STARTED",
  "timestamp": "2026-11-22T13:00:00Z",
  "device_type": "TV",
  "session_id": "swagger-session",
  "progress_seconds": 0
}
```

Успешный ответ — `201` с `event_id`.

## Результат

```bash
docker compose exec -T clickhouse clickhouse-client --user analytics --password analytics-local \
  --query "SELECT event_type, user_id, movie_id, timestamp FROM movie_events FINAL ORDER BY timestamp DESC LIMIT 50"
```

Чтобы увидеть пример из Swagger, добавь `WHERE user_id = 'swagger-demo'` перед `ORDER BY`.


## Очистка событий

Если генератор включён, выключи его командой `GENERATE_EVENTS=0 docker compose up -d --wait movie` и дай ClickHouse дочитать события из Kafka.

Команда удаляет все строки из `movie_events`.

```bash
docker compose exec -T clickhouse clickhouse-client \
  --user analytics --password analytics-local \
  --query "TRUNCATE TABLE movie_events"
```

## Тесты

```bash
MOVIE_PORT=0 docker compose -p soa-hw5-test run --build --rm test
```

Compose сам поднимет отдельный стенд. Сквозной тест отправит событие через HTTP и сверит все его поля в ClickHouse.

## Остановка

```bash
docker compose down
docker compose -p soa-hw5-test down
```

Вторая команда останавливает отдельный тестовый стенд.
