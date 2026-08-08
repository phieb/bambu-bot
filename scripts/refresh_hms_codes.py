#!/usr/bin/env python3
"""Re-fetch Bambu's official HMS/print-error catalogue into ``hms_data/``.

The printer only ever sends a *number* (Bambuddy hands us ``full_code``); the
human sentence behind it lives in Bambu's public catalogue, the same one Bambu
Studio queries. We bundle it instead of calling out at runtime: the bot must be
able to explain an error while the internet is down, and the payload barely
compresses to 60 kB per language.

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


def fetch(lang):
    # The endpoint 403s a bare urllib request — it wants a browser-ish UA.
    req = urllib.request.Request(URL.format(lang=lang),
                                 headers={"User-Agent": "Mozilla/5.0 (bambu-bot hms refresh)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    data = payload["data"]
    # device_hms = the 16-hex HMS codes, device_error = the 8-hex print_error
    # codes. Both are lists of {ecode, intro}; we only need the mapping.
    hms = {e["ecode"].upper(): e["intro"].strip()
           for e in data["device_hms"][lang] if e.get("ecode") and e.get("intro")}
    err = {e["ecode"].upper(): e["intro"].strip()
           for e in data["device_error"][lang] if e.get("ecode") and e.get("intro")}
    return {"lang": lang, "ver": data["device_hms"].get("ver"), "hms": hms, "err": err}


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
