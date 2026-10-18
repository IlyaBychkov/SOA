# HW2 — Marketplace: OpenAPI + CRUD

[Задание](marketplace_homework_openapi_crud.md).

Контракт API: [openapi/openapi.yaml](openapi/openapi.yaml)

## Докер

```bash
colima start
```

Из папки HW2 запуск:

```bash
docker compose up --build
```

[Документация](http://localhost:8000/docs).

Из папки HW2 остановка:

```bash
docker compose down
```

## Локальная генерация

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
fastapi-codegen --input openapi/openapi.yaml --output app/generated --disable-timestamp
```
