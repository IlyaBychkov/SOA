FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install -r requirements.txt

COPY openapi/ openapi/
RUN fastapi-codegen --input openapi/openapi.yaml --output app/generated --disable-timestamp

CMD ["uvicorn", "app.generated.main:app", "--host", "0.0.0.0", "--port", "8000"]
