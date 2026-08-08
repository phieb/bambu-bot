#!/usr/bin/env python3
"""Re-fetch Bambu's official HMS/print-error catalogue into ``hms_data/``.

The printer only ever sends a *number* (Bambuddy hands us ``full_code``); the
human sentence behind it lives in Bambu's public catalogue, the same one Bambu
Studio queries. We bundle it instead of calling out at runtime: the bot must be
able to explain an error while the internet is down, and the payload barely
compresses to 60 kB per language.

``?d=<serial prefix>`` scopes the catalogue to one machine — ``01P`` is the P1S
(our serial is ``01P00C571300842``; Bambuddy keys its action table off the same
three characters). Verified 2026-08-08: scoping only *filters* the code set, it
never changes the wording — of the ~3900 codes both lists share, **zero** differ
in text. The P1S list drops 35 codes for hardware it doesn't have and adds 4 it
alone knows (part-cooling fan ``0300-3100-…``, the Ethernet accessory, an SD-card
warning). So we fetch both and **merge**: the device list contributes its
exclusive codes, the generic one keeps an explanation available for anything an
odd firmware might emit. A wrong-but-plausible sentence isn't a risk here — the
texts are identical where they overlap.

Run it when codes look unknown (new firmware adds some):

    ./.venv/bin/python scripts/refresh_hms_codes.py

then commit the regenerated ``hms_data/hms_*.json.gz``.
"""
import gzip
import json
import os
import sys
import urllib.request

URL = "https://e.bambulab.com/query.php?lang={lang}"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hms_data")
# Serial prefix of the machine we print on. Other known values: 00M/00W = X1
# series, 03W/039 = H2D family. An unknown one answers `result: 201`.
DEVICE = "01P"


def _get(url):
    # The endpoint 403s a bare urllib request — it wants a browser-ish UA.
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (bambu-bot hms refresh)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    # `result: 0` is success; anything else comes with an empty string as data
    # (e.g. 201 for a device id Bambu doesn't publish a catalogue for).
    if payload.get("result") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"{url} → result {payload.get('result')}, no catalogue")
    return payload["data"]


def _codes(data, key, lang):
    """{ECODE: text} from one catalogue section (list of {ecode, intro})."""
    return {e["ecode"].upper(): e["intro"].strip()
            for e in data.get(key, {}).get(lang, [])
            if e.get("ecode") and e.get("intro")}


def fetch(lang, device=DEVICE):
    generic = _get(URL.format(lang=lang))
    scoped = _get(URL.format(lang=lang) + f"&d={device}")
    # device_hms = the 16-hex HMS codes, device_error = the 8-hex print_error
    # codes. Device entries last so they win on any future disagreement.
    hms = {**_codes(generic, "device_hms", lang), **_codes(scoped, "device_hms", lang)}
    err = {**_codes(generic, "device_error", lang), **_codes(scoped, "device_error", lang)}
    return {"lang": lang, "device": device,
            "ver": scoped.get("device_hms", {}).get("ver"), "hms": hms, "err": err}


def main(langs=("de", "en")):
    os.makedirs(OUT, exist_ok=True)
    for lang in langs:
        table = fetch(lang)
        path = os.path.join(OUT, f"hms_{lang}.json.gz")
        blob = json.dumps(table, ensure_ascii=False, sort_keys=True).encode()
        # mtime=0 → byte-identical output for unchanged input, so a re-run that
        # changes nothing produces no git diff.
        with open(path, "wb") as fh:
            fh.write(gzip.compress(blob, 9, mtime=0))
        print(f"{path}: {len(table['hms'])} HMS + {len(table['err'])} print-error codes "
              f"(ver {table['ver']}), {os.path.getsize(path) // 1024} kB")


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or ("de", "en"))
