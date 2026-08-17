from fastapi import FastAPI

app = FastAPI(title="Claim Checker POC")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
