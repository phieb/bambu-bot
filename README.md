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

A **DM with a MakerWorld link** finds-or-creates the sender's persistent Signal
group, then runs a small dialog state machine in that group. Each step is a
**numbered reply**; steps with only one option are skipped automatically:

1. **Profile** — if the model has several MakerWorld print profiles, ask which
   one (profiles made for the configured printer are flagged and counted).
2. **Plate(s)** — after importing the chosen profile, if it's a multi-plate
   project, ask which plate(s) to print (rendered plate thumbnails attached).
   Multiple plates can be selected at once → one queue job each.
3. **Colors** — for each selected plate, ask which AMS slot to use per filament
   (plate thumbnail + a generated color swatch attached). On reply the plate is
   **re-sliced for the target printer** (so a MakerWorld X1C slice doesn't land on
   a P1S) and queued via `POST /queue/` (auto-dispatch, gcode injection on).

One open dialog per group; a second link while one is open → "finish current
first". Every stage transition is idempotent (atomic claim). Unrecognized
senders / unregistered groups are silently ignored.

### Group commands (`!` prefix)

| Command | Does |
|---|---|
| `!progress` / `!status` | Live print state (%, layer, ETA) **+ a camera snapshot** |
| `!liste` / `!queue` | The current Bambuddy queue with status emoji |
| `!abbrechen` / `!cancel` | Drop the open dialog, else delete the last *pending* queue item (a running print is never stopped) |
| `!help` / `!hilfe` | Command overview |

When a queued print finishes (or fails), the bot messages the group that queued
it. Prints started through other channels aren't tracked, so they get no
Signal update.

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
| `BAMBUDDY_PRINTER_MODEL` | `P1S` (re-slice target + which profiles get flagged) |
| `BAMBUDDY_NOZZLE` | `0.4` |
| `BAMBU_GROUP_NAME` | `🖨️ Bambu Print Queue` |

## State (sqlite, `DB_PATH`)

- `groups(sender PK, group_id, group_name, created_at)` — persistent per-user group
- `jobs(id, group_id, sender, model_id, library_file_id, model_name, required_colors, ams_snapshot, stage, queue_item_id, profile_id, profiles, plates, pending_plates, plate_index, plate_name, …)` — two row kinds: one **dialog** per group (a non-terminal stage carrying the in-progress selection) and one **tracker** per queued plate (`stage='queued'`, watched for completion)

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
