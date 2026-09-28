from fastapi import FastAPI

from app.api.claims import router as claims_router
from app.api.documents import router as documents_router
from app.agents.claim_verifier_worker import run_background_verifier
import asyncio

app = FastAPI(title="Claim Checker POC")
app.include_router(documents_router)
app.include_router(claims_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("startup")
async def _startup_verifier():
    # launch background verifier task
    loop = asyncio.get_event_loop()
    app.state._verifier_stop = asyncio.Event()
    app.state._verifier_task = loop.create_task(run_background_verifier(app.state._verifier_stop))


@app.on_event("shutdown")
async def _shutdown_verifier():
    # stop background verifier gracefully
    stop = getattr(app.state, "_verifier_stop", None)
    task = getattr(app.state, "_verifier_task", None)
    if stop is not None:
        stop.set()
    if task is not None:
        await task
