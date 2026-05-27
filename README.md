# bambu-bot

Signal-driven 3D-print queue bot. A small HTTP service that an upstream
**dispatcher** — e.g. [signal-router](https://github.com/phieb/signal-router) —
forwards Signal messages to: it answers "is this mine?" via `/claims` and, if so,
does the work via `/receive`. It knows which senders it may act for, talks back
over a Signal REST API, and turns MakerWorld links into print jobs on
[Bambuddy](https://github.com/maziggy/bambuddy).

## Flow

```
Signal message → dispatcher → bambu-bot /claims → yes → bambu-bot /receive
                                                 → no  → not handled here
```

1. **DM with a MakerWorld link** → find-or-create the sender's persistent Signal
   group, import the model, snapshot the AMS, and ask (in the group) which slot
   to use per color — numbered reply.
2. **Numbered reply in a registered group** → map colors → `POST /queue/` on
   Bambuddy (auto-dispatch, gcode injection on).
3. One open dialog per group; a second link while one is open → "finish current
   first". Queueing is idempotent (atomic `awaiting_colors → queued` flip).

Unrecognized senders / unregistered groups are silently ignored.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/claims` | Side-effect-free predicate → `{"claims": bool}` |
| POST | `/receive` | Handle the message (background), returns `{"status":"accepted"}` |
| GET | `/health` | `{"status":"ok"}` |

All accept the Signal envelope as the bare object, `{envelope:...}`, or
`{body:{envelope:...}}` (whatever the dispatcher forwards).

## Config (env)

| Var | Default |
|---|---|
| `BAMBUDDY_URL` | `http://bambuddy:8010` |
| `SIGNAL_URL` | `http://signal-api:8080` |
| `SIGNAL_BOT_NUMBER` | _(required, e.g. `+431234567`)_ |
| `DB_PATH` | `/data/bambu.db` |
| `BAMBUDDY_PRINTER_ID` | `1` |
| `BAMBU_GROUP_NAME` | `🖨️ Bambu Print Queue` |

## State (sqlite, `DB_PATH`)

- `groups(sender PK, group_id, group_name, created_at)` — persistent per-user group
- `jobs(id, group_id, sender, model_id, library_file_id, model_name, required_colors, ams_snapshot, stage, …)`

## Deploy

Add `docker-compose.bambu-bot.yml` to a compose stack on the **same network** as
your dispatcher and your Signal REST API, set the env vars, then
`docker compose up -d bambu-bot`.

`SIGNAL_URL` must point at a Signal REST API exposing `/v2/send` and
`/v1/groups/{number}` (the bot sends messages and creates groups through it).

## Dev / tests

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests -q
```
