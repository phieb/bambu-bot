"""bambu-bot HTTP service. An upstream dispatcher asks /claims, then forwards to
/receive."""
import logging

from fastapi import BackgroundTasks, FastAPI, Request

import handlers
import store

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="bambu-bot")


@app.on_event("startup")
def _startup():
    store.init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


def _envelope(payload):
    """Accept either the bare envelope, or {envelope:...}, or {body:{envelope:...}}."""
    if not isinstance(payload, dict):
        return {}
    if "envelope" in payload:
        return payload["envelope"]
    body = payload.get("body")
    if isinstance(body, dict) and "envelope" in body:
        return body["envelope"]
    return payload


@app.post("/claims")
async def claims(request: Request):
    payload = await request.json()
    return {"claims": handlers.claims(_envelope(payload))}


@app.post("/receive")
async def receive(request: Request, background: BackgroundTasks):
    payload = await request.json()
    background.add_task(handlers.handle, _envelope(payload))
    return {"status": "accepted"}
