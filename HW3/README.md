# HW3 — Flight Booking

## Файлы

- `app/booking.py` — REST API, модели запросов и ответов, gRPC-клиент и повторы вызовов.
- `app/flight.py` — gRPC API, проверка ключа, резервирование мест и Redis-кеш.
- `app/db.py` — подключение к PostgreSQL и запуск миграций.
- `migrations/env.py` — подключение Alembic к базе.
- `migrations/flight/`, `migrations/booking/` — SQL-миграции и их Python-обёртки.
- `proto/flight/v1/flight.proto` — gRPC-контракт; `proto/buf.yaml` — настройки его проверки.
- `docs/database-schema.dbml`, `docs/database-schema.png`, `docs/database-schema.svg` — ER-диаграмма.
- `compose.yaml`, `Dockerfile`, `requirements.txt` — запуск контейнеров, сборка и зависимости.
- `.env` — локальный API Key.

## Запуск

Все команды ниже выполняются из `HW3` в одном терминале. Локальный Python и дополнительные утилиты не нужны.

```bash
docker compose up --build -d --wait
curl -sS "http://localhost:8000/health"
docker compose ps
```

Ожидается `{"status":"ok"}`. Если сервер ещё запускается, повторить запрос через пару секунд. Swagger: [http://localhost:8000/docs](http://localhost:8000/docs).

Таблицы и применённые миграции:

```bash
docker compose exec -T flight-db psql -U flight -d flights -c '\dt' -c 'TABLE alembic_version;'
docker compose exec -T booking-db psql -U booking -d bookings -c '\dt' -c 'TABLE alembic_version;'
```

## Рейс для проверки

Используется готовый рейс `SU1234`: SVO → LED, 1 декабря 2026 года, 100 мест по 5000 рублей. Он уже добавляется миграцией. Количества мест ниже указаны для исходного состояния без активных бронирований.

```bash
FLIGHT_ID="11111111-1111-4111-8111-111111111111"
```

## REST: поиск и получение рейса

Оба поиска должны содержать `SU1234`; получение по ID — показать 100 доступных мест.

```bash
curl -sS "http://localhost:8000/flights?origin=SVO&destination=LED"
curl -sS "http://localhost:8000/flights?origin=SVO&destination=LED&date=2026-12-01"
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
```

## Redis: промах, попадание и TTL

Удалить только ключи демонстрационных запросов, затем выполнить каждый запрос дважды:

```bash
docker compose exec -T redis redis-cli DEL "flight:$FLIGHT_ID" "search:SVO:LED:2026-12-01"
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
curl -sS "http://localhost:8000/flights?origin=SVO&destination=LED&date=2026-12-01"
curl -sS "http://localhost:8000/flights?origin=SVO&destination=LED&date=2026-12-01"
docker compose exec -T redis redis-cli TTL "flight:$FLIGHT_ID"
docker compose exec -T redis redis-cli TTL "search:SVO:LED:2026-12-01"
docker compose logs --tail 20 flight
```

TTL — положительное число не больше 300. В логах — `cache miss`, затем `cache hit` для каждого ключа.

## REST: создание, получение и список бронирований

```bash
curl -sS -X POST "http://localhost:8000/bookings" \
    -H 'Content-Type: application/json' -d @- <<JSON
{
        "user_id": "defense-$FLIGHT_ID",
        "flight_id": "$FLIGHT_ID",
        "passenger_name": "Student",
        "passenger_email": "student@example.org",
        "seat_count": 2
}
JSON
```

Скопировать `id` бронирования из ответа:

```bash
BOOKING_ID="сюда UUID бронирования"

docker compose exec -T redis redis-cli EXISTS "flight:$FLIGHT_ID" "search:SVO:LED:2026-12-01"
curl -sS "http://localhost:8000/bookings/$BOOKING_ID"
curl -sS "http://localhost:8000/bookings?user_id=defense-$FLIGHT_ID"
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
curl -sS "http://localhost:8000/flights?origin=SVO&destination=LED&date=2026-12-01"
```

Бронирование — `CONFIRMED`, стоимость — 10000, осталось 98 мест. `EXISTS` сразу после создания возвращает `0`: оба старых ключа удалены.

## REST: отмена

```bash
curl -sS -w '\nHTTP %{http_code}\n' -X POST "http://localhost:8000/bookings/$BOOKING_ID/cancel"
docker compose exec -T redis redis-cli EXISTS "flight:$FLIGHT_ID" "search:SVO:LED:2026-12-01"
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
curl -sS -w '\nHTTP %{http_code}\n' -X POST "http://localhost:8000/bookings/$BOOKING_ID/cancel"
```

Первая отмена — `200`, статус `CANCELLED`, ключи удалены, снова 100 мест. Повторная отмена — `409`.

## REST: ошибки

По порядку: отсутствующий рейс — `404`, отсутствующее бронирование — `404`, неверный IATA-код — `422`, ноль мест — `422`, недостаточно мест — `409`.

```bash
curl -sS -w '\nHTTP %{http_code}\n' "http://localhost:8000/flights/00000000-0000-0000-0000-000000000001"
curl -sS -w '\nHTTP %{http_code}\n' "http://localhost:8000/bookings/00000000-0000-0000-0000-000000000001"
curl -sS -w '\nHTTP %{http_code}\n' "http://localhost:8000/flights?origin=bad&destination=LED"
curl -sS -w '\nHTTP %{http_code}\n' -X POST "http://localhost:8000/bookings" \
    -H 'Content-Type: application/json' -d @- <<JSON
{
        "user_id": "defense-$FLIGHT_ID",
        "flight_id": "$FLIGHT_ID",
        "passenger_name": "Student",
        "passenger_email": "student@example.org",
        "seat_count": 0
}
JSON
curl -sS -w '\nHTTP %{http_code}\n' -X POST "http://localhost:8000/bookings" \
    -H 'Content-Type: application/json' -d @- <<JSON
{
        "user_id": "defense-$FLIGHT_ID",
        "flight_id": "$FLIGHT_ID",
        "passenger_name": "Student",
        "passenger_email": "student@example.org",
        "seat_count": 101
}
JSON
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
```

После неуспешных запросов по-прежнему 100 мест.

## Повторы при недоступности Flight

Ожидается `503` (`UNAVAILABLE`) или `504` (`DEADLINE_EXCEEDED`). В логах Booking — `retry 2/3` и `retry 3/3`. Паузы между попытками — 0,1 и 0,2 секунды; при таймаутах весь запрос занимает около 15 секунд.

```bash
docker compose stop flight
curl -sS -w '\nHTTP %{http_code}, %{time_total} seconds\n' "http://localhost:8000/flights/$FLIGHT_ID"
docker compose logs --tail 15 booking
docker compose up -d --wait flight
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
```

## Таймаут после успешного резервирования

Временный триггер задерживает запись резервации на 6 секунд. Бронирование должно создаться после повторного вызова: 3 места списываются один раз, остаётся 97.

```bash
docker compose exec -T flight-db psql -v ON_ERROR_STOP=1 -v flight_id="$FLIGHT_ID" -U flight -d flights <<'SQL'
CREATE FUNCTION defense_delay() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_sleep(6);
    RETURN NEW;
END $$;
CREATE TRIGGER defense_delay BEFORE INSERT ON seat_reservations
FOR EACH ROW WHEN (NEW.flight_id = :'flight_id'::uuid)
EXECUTE FUNCTION defense_delay();
SQL

curl -sS -X POST "http://localhost:8000/bookings" \
    -H 'Content-Type: application/json' -d @- <<JSON
{
        "user_id": "defense-$FLIGHT_ID",
        "flight_id": "$FLIGHT_ID",
        "passenger_name": "Student",
        "passenger_email": "student@example.org",
        "seat_count": 3
}
JSON
```

Скопировать `id` бронирования из ответа:

```bash
BOOKING_ID="сюда UUID бронирования"
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
docker compose logs --tail 15 booking

docker compose exec -T flight-db psql -v ON_ERROR_STOP=1 -U flight -d flights <<'SQL'
DROP TRIGGER defense_delay ON seat_reservations;
DROP FUNCTION defense_delay();
SQL
curl -sS -X POST "http://localhost:8000/bookings/$BOOKING_ID/cancel"
```

## Откат Booking и возврат мест при ошибке сохранения

Временный триггер вызывает ошибку именно при подтверждении транзакции Booking. POST должен вернуть `503`, а у рейса останется 100 мест.

```bash
docker compose exec -T booking-db psql -v ON_ERROR_STOP=1 -v flight_id="$FLIGHT_ID" -U booking -d bookings <<'SQL'
CREATE FUNCTION defense_reject_commit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Demonstration: booking commit failed';
END $$;
CREATE CONSTRAINT TRIGGER defense_reject_commit AFTER INSERT ON bookings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW WHEN (NEW.flight_id = :'flight_id'::uuid)
EXECUTE FUNCTION defense_reject_commit();
SQL

curl -sS -w '\nHTTP %{http_code}\n' -X POST "http://localhost:8000/bookings" \
    -H 'Content-Type: application/json' -d @- <<JSON
{
        "user_id": "defense-$FLIGHT_ID",
        "flight_id": "$FLIGHT_ID",
        "passenger_name": "Student",
        "passenger_email": "student@example.org",
        "seat_count": 2
}
JSON
curl -sS "http://localhost:8000/flights/$FLIGHT_ID"
curl -sS "http://localhost:8000/bookings?user_id=defense-$FLIGHT_ID"

docker compose exec -T booking-db psql -v ON_ERROR_STOP=1 -U booking -d bookings <<'SQL'
DROP TRIGGER defense_reject_commit ON bookings;
DROP FUNCTION defense_reject_commit();
SQL
```

## Остановка

Бронирования из сценария отменены, места возвращены. Записи об отменённых бронированиях остаются в базе.

```bash
docker compose stop
```
