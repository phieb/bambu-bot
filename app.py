"""bambu-bot HTTP service. An upstream dispatcher asks /claims, then forwards to
/receive."""
import logging

from fastapi import BackgroundTasks, FastAPI, Request

import classify
import handlers
import store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bambu-bot")
app = FastAPI(title="bambu-bot")


@app.on_event("startup")
def _startup():
    store.init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/claims")
async def claims(request: Request):
    """Side-effect-free routing probe. Claims if ANY forwarded envelope is ours.

    Accepts a single envelope or a list in any wrap shape (see
    ``classify.to_envelopes``), so it parses identically to ``/receive``.
    """
    payload = await request.json()
    envelopes = classify.to_envelopes(payload)
    claimed = any(handlers.claims(e) for e in envelopes)
    logger.info("/claims envelopes=%d claimed=%s", len(envelopes), claimed)
    return {"claims": claimed}


@app.post("/receive")
async def receive(request: Request, background: BackgroundTasks):
    payload = await request.json()
    envelopes = classify.to_envelopes(payload)
    for env in envelopes:
        background.add_task(handlers.handle, env)
    return {"status": "accepted"}
