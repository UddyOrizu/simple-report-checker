from fastapi import FastAPI

from app.api.claims import router as claims_router
from app.api.documents import router as documents_router

app = FastAPI(title="Claim Checker POC")
app.include_router(documents_router)
app.include_router(claims_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
