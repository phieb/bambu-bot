# Auto-eject setup (Bambuddy P1S snippet)

The bot **no longer builds or injects** the eject G-code itself. The eject now
runs as a **Bambuddy per-model G-code snippet**, injected server-side at dispatch.
The bot only:

1. toggles it (`!eject on` / `!eject off`), and
2. **pre-screens the print height** so a part too tall for the bender is refused
   before it reaches the queue.

So for auto-eject to actually fire, two things must be true on the Bambuddy
side: (1) it must be a **build that evaluates G-code placeholders** (see the
prerequisite below), and (2) the P1S snippet must be configured once.

## Prerequisite: a Bambuddy that evaluates placeholder arithmetic

The snippet computes the sweep height per print with an expression:
`G0 Z{clamp(max_z_height - 4, 1.5, 176)}`. **Stock / upstream Bambuddy cannot do
this.** Upstream [`maziggy/bambuddy`](https://github.com/maziggy/bambuddy) has the
base end-G-code injection (#422), but a placeholder like `{clamp(...)}` is left in
the file **verbatim** — the printer then chokes on `G0 Z{clamp(...)}` and the
eject is broken (or worse, runs a garbage move). The arithmetic + `clamp/min/max`
evaluation is a **fork feature**, so you must run a Bambuddy that has it.

- **Source:** fork [`phieb/bambuddy`](https://github.com/phieb/bambuddy), branch
  **`feature/gcode-injection-arithmetic`**. On top of upstream's #422 pipeline it
  adds: placeholder **arithmetic** + `min/max/clamp`, per-model start/end snippet
  injection for VP/Studio **and** API jobs, `.gcode.md5` sidecar recompute (P1S
  rejects the file otherwise), insertion **before** `; EXECUTABLE_BLOCK_END`, and
  the per-model `max_height_mm` guard. (The injection + P1S fixes *without* the
  arithmetic are the upstream-PR subset, branch `feature/vp-gcode-injection`,
  #1516; the `{clamp(...)}` this snippet needs lives only on the arithmetic
  branch.)
- **Deployed:** the farm host (`muscle`) runs this as the Docker image
  **`bambuddy:0.2.4.5-inject`** — a thin overlay on the official
  `ghcr.io/maziggy/bambuddy:0.2.4.5` that copies in the three patched files
  (`threemf_tools.py`, `manager.py`, `print_scheduler.py`). The overlay is
  **lossless**: those files are byte-identical between the branch's base (`dev`)
  and `v0.2.4.5`, so only the injection hunks change.
- **Build it yourself** — the overlay (matches what muscle runs; builds in
  seconds, only a COPY layer; lives in `~/docker/bambuddy-inject-patch/` on muscle):
  ```Dockerfile
  FROM ghcr.io/maziggy/bambuddy:0.2.4.5
  COPY threemf_tools.py   /app/backend/app/utils/threemf_tools.py
  COPY manager.py         /app/backend/app/services/virtual_printer/manager.py
  COPY print_scheduler.py /app/backend/app/services/print_scheduler.py
  ```
  Take the three files from a checkout of `feature/gcode-injection-arithmetic`,
  then `docker build -t bambuddy:0.2.4.5-inject .`. (Or build the whole app from
  that branch: `docker build -t bambuddy:inject .`.)
- **Full placeholder reference:** that repo's
  [`docs/gcode-injection.md`](https://github.com/phieb/bambuddy/blob/feature/gcode-injection-arithmetic/docs/gcode-injection.md)
  — the variable set (`max_z_height`, …), arithmetic rules, `clamp/min/max`, and
  the `max_height_mm` guard.
- **Verify your build supports it:** after configuring the snippet (below),
  dispatch any short P1S job with injection on and pull the dispatched
  `.gcode.3mf`; the `G0 Z{clamp(...)}` lines must come out as **numbers**
  (e.g. `G0 Z11.5`), not the literal `{clamp(...)}` text.

## The file

[`eject_snippet_P1S.gcode`](eject_snippet_P1S.gcode) — the canonical end snippet
(this is the source of truth; keep it and Bambuddy in sync). It uses Bambuddy
placeholders so the sweep height tracks each print:
`Z = {clamp(max_z_height - 4, 1.5, 176)}`.

## How to patch it into Bambuddy

### Option A — UI (recommended)

1. Bambuddy → **Settings → G-code snippets**.
2. Pick model **`P1S`** (the key must match the printer's `model` string
   **exactly** — a mismatch silently injects nothing).
3. Paste the whole of `eject_snippet_P1S.gcode` into the **end_gcode** field.
4. Set **`max_height_mm` = `180`** for the model (the height guard: Bambuddy
   fails any job whose top-layer Z exceeds this — or can't be determined — so a
   too-tall part can't drive the bed into the gantry).
5. Save. Leave `start_gcode` empty.

### Option B — settings JSON / DB

Snippets are stored as JSON keyed by model. The P1S entry should look like:

```json
{
  "P1S": {
    "start_gcode": "",
    "end_gcode": "<contents of eject_snippet_P1S.gcode>",
    "max_height_mm": 180
  }
}
```

## When it fires

- **Bambu Studio "Send" / Virtual Printer uploads** opt into injection
  automatically (`gcode_injection = true`).
- **Bot-queued jobs** get `gcode_injection` = the `!eject on/off` state. So with
  `!eject on` the bot queues with the flag on and Bambuddy injects this snippet;
  with `!eject off` it queues with the flag off and nothing is injected.

> **Toggle timing:** injection happens at *dispatch*, and the per-item flag is
> fixed when the job is queued. `!eject off` only affects **future** jobs — items
> already queued with the flag on still eject. `!liste` tags each item 🧹 / ✋ so
> you can see which.

## Tunables (edit the snippet, re-paste)

| Knob | In the snippet | Note |
|---|---|---|
| Nozzle cool-down target | `M109 R50` | 50 °C is below PLA's glass transition → no melt marks. Raise (`R80`) for shorter cycles at the cost of finish; lower for stubborn material. ~1–3 min with the fan. |
| Sweep grab height | `clamp(max_z_height - 4, 1.5, 176)` | `-4` = overshoot (bed sits 4 mm above the print top so the foam grabs the body). `1.5`/`176` clamp to the safe Z range. |
| Lane order / X positions | `X128 → 98 → 158 → 50 → 206` | Centre lane first (square-on push), then fan outward. Tuned for a ~55 mm foam sweeper; adjust to your hardware. |
| Bender depth / cycles | `G0 Z240` / `Z200` block | Flexes the bed against the Farmloop clip to crack adhesion. |
| Height guard | `max_height_mm` (Bambuddy setting, not the snippet) | Mirror the bot's `EJECT_MAX_HEIGHT_MM` (default 180). |

## Bench test

[`../eject_test.gcode`](../eject_test.gcode) runs the same sequence stand-alone
(homes first, fixed `H=50` example) on an **empty** bed to dial in the geometry
before trusting it on a real print.
