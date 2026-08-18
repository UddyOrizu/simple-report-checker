import asyncio
import json
import uuid

from app.api.documents import stream_events
from app.events.broadcaster import broadcaster


async def test_sse_delivers_every_event_type_in_order():
    """The Phase 7 verification gate: SSE delivers every event type (ingest_complete,
    claim_extracted, claim_verified, verification_complete — plus ingest_progress) in order.
    Exercises the actual subscribe -> format -> yield logic in app.api.documents.stream_events
    directly against the real broadcaster; the wire-level SSE framing itself is sse_starlette's
    concern, not re-tested here (see the manual curl smoke test note in the PR for that layer)."""
    document_id = uuid.uuid4()
    events_to_send = [
        {"event": "ingest_progress", "pages_done": 5, "pages_total": 20},
        {"event": "ingest_complete", "page_count": 20, "chunk_count": 40, "table_count": 2},
        {"event": "claim_extracted", "claim_id": str(uuid.uuid4()), "claim_text": "Revenue grew 12%"},
        {"event": "claim_verified", "claim_id": str(uuid.uuid4()), "final_verdict": "supported"},
        {"event": "verification_complete", "document_id": str(document_id)},
    ]

    gen = stream_events(document_id)

    async def publisher():
        await asyncio.sleep(0.05)  # let the generator subscribe first
        for event in events_to_send:
            await broadcaster.publish(document_id, event)

    publisher_task = asyncio.create_task(publisher())

    received = []
    for _ in events_to_send:
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=2)
        received.append(json.loads(chunk["data"]))

    await publisher_task
    await gen.aclose()

    assert received == events_to_send
    assert [e["event"] for e in received] == [
        "ingest_progress", "ingest_complete", "claim_extracted", "claim_verified", "verification_complete"
    ]


async def test_sse_generator_unsubscribes_on_close():
    document_id = uuid.uuid4()
    gen = stream_events(document_id)

    # __anext__() won't return until an event is published (it yields only after queue.get()
    # resolves) — run it as a cancellable task and give it a moment to reach that suspend point
    next_task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)

    assert len(broadcaster._queues.get(document_id, [])) == 1

    next_task.cancel()
    try:
        await next_task
    except asyncio.CancelledError:
        pass

    await gen.aclose()

    assert document_id not in broadcaster._queues
