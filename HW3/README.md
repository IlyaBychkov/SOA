# HW3 — Flight Booking (7 баллов)

Два синхронных сервиса на Python: Booking (FastAPI REST) → Flight (gRPC).
У каждого своя PostgreSQL. Рабочая версия для пунктов 1–7.
REST-обработчики, gRPC и запросы к БД синхронные.
Одновременные запросы обрабатываются потоками; async остаётся только в служебном lifespan FastAPI.
Для каждого обращения к БД открывается отдельное подключение без пула.
Booking `/health` проверяет только свою БД. Compose ожидает готовности баз и Flight перед запуском Booking.

## Запуск

Из папки `HW3`:

```bash
docker compose up --build
```

Миграции Alembic применяются автоматически. Данные сохраняются между запусками.
Swagger: http://localhost:8000/docs. gRPC доступен между контейнерами по адресу `flight:50051`.
Остановка: `docker compose stop`.

Демонстрационный рейс: `11111111-1111-4111-8111-111111111111`, SU1234,
SVO → LED, 2026-12-01 10:00 UTC, 100 мест, 5000 RUB за место.

```bash
curl 'http://localhost:8000/flights?origin=SVO&destination=LED&date=2026-12-01'
curl -X POST http://localhost:8000/bookings \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"student","flight_id":"11111111-1111-4111-8111-111111111111","passenger_name":"Ivan Ivanov","passenger_email":"ivan@example.org","seat_count":2}'
curl 'http://localhost:8000/bookings?user_id=student'
curl http://localhost:8000/bookings/BOOKING_ID
curl -X POST http://localhost:8000/bookings/BOOKING_ID/cancel
```

`date` необязателен. Поиск возвращает SCHEDULED, даты интерпретируются в UTC.
Отсутствующий объект → 404; нехватка мест, неактивный рейс, повторная отмена → 409;
ошибка входных данных → 422; недоступность Flight → 503; таймаут → 504.

## Файлы

- `app/booking.py` — REST, схемы запросов/ответов, gRPC-клиент, бронирования.
- `app/flight.py` — четыре gRPC-метода и запуск сервера.
- `app/db.py`, `migrations/` — подключение к БД и независимые миграции двух сервисов.
  Создание таблиц описано в `001_initial.sql`, соседний `.py` запускает SQL через Alembic.
- `proto/flight/v1/flight.proto` — четыре RPC, Timestamp, enum и коды ошибок.
  Генерация через `grpc_tools.protoc` выполняется при сборке образа.
- `docs/database-schema.dbml` — ER-диаграмма для dbdiagram.io.
  Откройте https://dbdiagram.io/d и вставьте содержимое файла в левый редактор.
  `docs/database-schema.png` — та же схема картинкой.

## Что входит в версию на 7 баллов

1. `.proto`: четыре RPC, Timestamp, enum и описания бизнес-ошибок.
2. ER-диаграмма и ограничения таблиц.
3. REST-сервис Booking, gRPC-сервис Flight, две PostgreSQL и автоматические миграции.
4. Создание и отмена бронирования с вызовами Flight по gRPC.
5. Транзакции и блокировки мест; компенсация при неуспешном сохранении Booking.
6. Аутентификация всех gRPC-методов по API Key из переменных окружения.
7. Redis: кеш рейсов и поиска с TTL 5 минут, очистка при изменении мест и логи hit/miss.

Создание Booking: GetFlight → ReserveSeats → сохранение бронирования и стоимости.
Отмена Booking: ReleaseReservation → смена статуса на CANCELLED.
При ошибке gRPC отмена прерывается, статус Booking не меняется.

Если сохранение Booking или подтверждение транзакции завершилось ошибкой,
Booking вызывает ReleaseReservation. Если сам ReserveSeats завершился ошибкой,
сохранение Booking не начинается. Отмена блокирует строку Booking до конца транзакции.

Операции Flight с местами и резервациями выполняются в одной транзакции;
SELECT FOR UPDATE защищает проверку мест и повторный возврат.
Повторов RPC нет. Повтор ReserveSeats с тем же booking_id не поддерживается.

Это две независимые БД, а не общая транзакция: при аварии процесса или недоступности
Flight компенсация не гарантирована. Неоднозначный результат ReserveSeats при таймауте
отдельно не обрабатывается: места могут остаться занятыми без Booking.
Ошибка компенсации записывается в лог с booking_id.
Если подтверждение отмены в Booking упало после возврата мест, отмену можно повторить:
ReleaseReservation не возвращает места дважды.

## Redis

Flight подключается к Redis по `REDIS_URL`, заданному в Compose.
GetFlight кешируется под ключом `flight:{id}`, SearchFlights —
`search:{origin}:{destination}:{date}` (без даты используется `all`).
В Redis сохраняются готовые Protobuf-ответы. Все ключи имеют TTL 300 секунд.

При запросе сначала читается Redis; при miss — PostgreSQL, затем ответ записывается в Redis.
В логах Flight видны `cache hit` и `cache miss`.
После успешного ReserveSeats или ReleaseReservation очищается весь кеш Flight:
так устаревшие данные удаляются и из отдельных рейсов, и из всех вариантов поиска.
Redis выделен только под этот кеш, других данных в нём нет.

Чтение и заполнение кеша, изменение мест и очистка выполняются под одной блокировкой потоков.
Это не даёт параллельному чтению записать старые данные после очистки.
Решение рассчитано на один процесс Flight; его RPC выполняются последовательно.
Redis запускается без отдельного тома: кеш можно заполнить заново из PostgreSQL.

## API Key

Booking передаёт ключ в metadata `x-api-key` при каждом gRPC-вызове, включая компенсацию.
Flight проверяет ключ одним interceptor перед всеми четырьмя методами.
Без ключа или с неверным ключом RPC завершается с UNAUTHENTICATED.
Healthcheck Flight также передаёт ключ.

Оба сервиса читают `GRPC_API_KEY` из окружения. Compose задаёт демонстрационное значение.
Чтобы задать своё, создайте рядом с `compose.yaml` файл `.env`:

```dotenv
GRPC_API_KEY=my-local-key
```

Пустой ключ запрещён при запуске приложения. REST-клиенту этот ключ передавать не нужно:
он используется между Booking и Flight. Ошибка аутентификации Flight возвращается REST-клиенту как 502.

## Python в VS Code

Откройте папку `SOA`: для неё настроено отдельное окружение `SOA/.venv`.
Создание окружения и установка зависимостей из папки `SOA`:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r HW3/requirements.txt
cd HW3
../.venv/bin/python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. proto/flight/v1/flight.proto
```

Последняя команда создаёт Python-код и описания типов для редактора.
Повторяйте её после изменения `.proto`. В Docker генерация выполняется автоматически.

## ER-диаграмма

![ER-диаграмма](docs/database-schema.png)

Flight DB содержит `flights` и `seat_reservations`, Booking DB — `bookings`.
SQL FK есть только между `seat_reservations.flight_id` и `flights.id`.
Две связи с `bookings` показаны логически: SQL FK между разными БД нет.
