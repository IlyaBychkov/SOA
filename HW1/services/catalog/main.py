from fastapi import FastAPI

app = FastAPI(title="Catalog Service")


@app.get("/health", status_code=200)
def health():
    return {"status": "ok"}
