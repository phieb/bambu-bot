"""Tiny message catalog for bilingual (de/en) bot replies.

Every user-facing string lives here keyed by a short name with a ``de`` and an
``en`` template. ``t(lang, key, **kw)`` picks the language (falling back to the
default — German — for an unknown language or a missing translation) and
``str.format``-fills the placeholders. The German wording is the source of
truth; English mirrors it. The bot's *default* language stays German: a group is
only English after an explicit ``!english`` switch.

Command keywords themselves are parsed bilingually in ``classify`` (``!list`` ==
``!liste``), so only the displayed text is translated here, not the commands.
"""

DEFAULT_LANG = "de"
LANGS = ("de", "en")


def normalize(lang):
    """Coerce anything into a supported language code (default German)."""
    return lang if lang in LANGS else DEFAULT_LANG


_M = {
    # ----- intake / busy -----
    "busy_link": {
        "de": "⏳ Du hast noch einen offenen Job — bitte konfigurier den zuerst "
              "(Profil/Plate/Farben), dann schick den nächsten Link.",
        "en": "⏳ You still have an open job — please configure it first "
              "(profile/plate/colors), then send the next link.",
    },
    "busy_model": {
        "de": "⏳ Du hast noch einen offenen Job — bitte konfigurier den zuerst, "
              "dann schick das nächste Modell.",
        "en": "⏳ You still have an open job — please configure it first, "
              "then send the next model.",
    },
    "resolve_failed": {
        "de": "❌ Konnte den Link nicht auflösen.",
        "en": "❌ Couldn't resolve the link.",
    },
    "import_failed": {
        "de": "❌ Konnte das Modell nicht importieren.",
        "en": "❌ Couldn't import the model.",
    },
    "file_processing": {
        "de": '⬆️ „{name}" wird verarbeitet …',
        "en": '⬆️ Processing „{name}" …',
    },
    "file_load_failed_signal": {
        "de": "❌ Konnte die Datei nicht von Signal laden.",
        "en": "❌ Couldn't load the file from Signal.",
    },
    "url_loading": {
        "de": '🌐 „{name}" wird vom Link geladen …',
        "en": '🌐 Loading „{name}" from the link …',
    },
    "url_load_failed": {
        "de": "❌ Konnte die Datei vom Link nicht laden.",
        "en": "❌ Couldn't load the file from the link.",
    },
    "thingiverse_loading": {
        "de": "🌐 Thingiverse-Modell {thing_id} wird geladen …",
        "en": "🌐 Loading Thingiverse model {thing_id} …",
    },
    "thingiverse_failed": {
        "de": "❌ Konnte das Thingiverse-Modell nicht laden.",
        "en": "❌ Couldn't load the Thingiverse model.",
    },
    "thingiverse_no_files": {
        "de": "❌ Das Thingiverse-Modell hat keine druckbaren Dateien (.stl/.3mf).",
        "en": "❌ The Thingiverse model has no printable files (.stl/.3mf).",
    },
    "zip_no_files": {
        "de": "❌ Im ZIP waren keine druckbaren Dateien.",
        "en": "❌ The ZIP had no printable files.",
    },
    "file_intake_failed": {
        "de": "❌ Da ist beim Einreihen was schiefgelaufen.",
        "en": "❌ Something went wrong while queuing.",
    },

    # ----- pre-sliced gcode gate (on intake) -----
    "gcode_for_other_named": {
        "de": "ist für **{for_what}** geslict, nicht für den {model}",
        "en": "is sliced for **{for_what}**, not for the {model}",
    },
    "gcode_other_device": {
        "de": "ein anderes Gerät (ID {model})",
        "en": "another device (ID {model})",
    },
    "gcode_raw_unverified": {
        "de": "ist rohes/ungeprüftes G-Code — ich kann nicht erkennen, für welchen "
              "Drucker es geslict wurde, und falscher G-Code kann den {model} beschädigen",
        "en": "is raw/unverified G-code — I can't tell which printer it was sliced "
              "for, and the wrong G-code can damage the {model}",
    },
    "gcode_rejected": {
        "de": '🚫 „{name}" {why}. Bitte in Bambu Studio für den {model} slicen, oder '
              'schick die .3mf/.stl, dann slice ich es selbst passend.',
        "en": '🚫 „{name}" {why}. Please slice it in Bambu Studio for the {model}, or '
              "send the .3mf/.stl and I'll slice it correctly myself.",
    },
    "queued_presliced": {
        "de": '✅ „{name}" ist in der Queue (vorgeslict)! Ich sag Bescheid, wenn er fertig ist.',
        "en": '✅ „{name}" is in the queue (pre-sliced)! I\'ll let you know when it\'s done.',
    },
    "label_note": {
        "de": '„{name}": {note}',
        "en": '„{name}": {note}',
    },

    # ----- replies / dialog -----
    "no_open_job": {
        "de": "Es gibt gerade keinen offenen Job. Schick mir ein Modell — "
              "MakerWorld-Link oder eine Datei (.3mf/.gcode/.stl/.zip).",
        "en": "There's no open job right now. Send me a model — a MakerWorld link "
              "or a file (.3mf/.gcode/.stl/.zip).",
    },
    "profile_pick_invalid": {
        "de": "⚠️ Bitte eine Zahl zwischen 1 und {n} schicken (welches Profil).",
        "en": "⚠️ Please send a number between 1 and {n} (which profile).",
    },
    "profile_load_failed": {
        "de": "❌ Konnte das Profil nicht laden.",
        "en": "❌ Couldn't load the profile.",
    },
    "plate_pick_invalid": {
        "de": "⚠️ Bitte eine oder mehrere Zahlen zwischen 1 und {n} schicken "
              "(welche Plates), z.B. „1“ oder „1 3“.",
        "en": "⚠️ Please send one or more numbers between 1 and {n} "
              '(which plates), e.g. „1“ or „1 3“.',
    },
    "generic_failed": {
        "de": "❌ Da ist was schiefgelaufen.",
        "en": "❌ Something went wrong.",
    },
    "colors_noted": {
        "de": '👍 Farben für „{label}" notiert: {nums}\n➡️ Weiter mit dem nächsten Plate:',
        "en": '👍 Colors for „{label}" noted: {nums}\n➡️ On to the next plate:',
    },

    # ----- queue safety gate -----
    "queue_foreign": {
        "de": '🚫 „{label}" ist für {whose} geslict, nicht für den {model} — ich reihe '
              "nur {model}-Gcode ein (kein Umkonvertieren von Fremddruckern). Schick mir "
              "bitte eine für den {model} geslicte Datei oder ein {model}-Profil.",
        "en": '🚫 „{label}" is sliced for {whose}, not for the {model} — I only queue '
              "{model} G-code (no converting from other printers). Please send me a file "
              "sliced for the {model} or a {model} profile.",
    },
    "queue_foreign_whose_named": {
        "de": "„{hint}“",
        "en": "„{hint}“",
    },
    "queue_foreign_whose_other": {
        "de": "einen anderen Drucker",
        "en": "another printer",
    },
    "queue_nozzle_mismatch": {
        "de": '🚫 „{label}" ist für eine {want} mm-Düse geslict, montiert ist aber eine '
              "{mounted} mm-Düse. Bitte die passende Düse montieren (und am Drucker "
              "einstellen) oder die Datei für {mounted} mm neu slicen.",
        "en": '🚫 „{label}" is sliced for a {want} mm nozzle, but a {mounted} mm nozzle '
              "is fitted. Please mount the matching nozzle (and set it on the printer) "
              "or re-slice the file for {mounted} mm.",
    },
    "eject_height_unknown": {
        "de": "🚫 Höhe nicht ermittelbar — sicherheitshalber verworfen (Auswerfer an). "
              "Mit „!eject off“ und erneut schicken druckt's ohne Auswerfer.",
        "en": "🚫 Height couldn't be determined — discarded to be safe (eject on). "
              "Send „!eject off“ and resend to print without the ejector.",
    },
    "eject_too_tall": {
        "de": "🚫 {h:.0f} mm hoch, max {max:.0f} mm mit Auswerfer (sonst fährt das Bett "
              "in den Bender) — verworfen. Tools ab, oder „!eject off“ und erneut schicken.",
        "en": "🚫 {h:.0f} mm tall, max {max:.0f} mm with the ejector (otherwise the bed "
              "drives into the bender) — discarded. Remove the tools, or „!eject off“ and resend.",
    },

    # ----- !eject -----
    "eject_on": {
        "de": "🧹 Auto-Auswurf ist **an** (max {max:.0f} mm Druckhöhe). Nach jedem Druck "
              "wirft der Drucker selbst aus, der nächste Job startet automatisch. Zu hohe "
              "Drucke werden gesperrt. Tools physisch montiert? Mit „!eject off“ wieder aus.",
        "en": "🧹 Auto-eject is **on** (max {max:.0f} mm print height). After each print "
              "the printer ejects by itself and the next job starts automatically. Prints "
              "that are too tall are blocked. Tools physically mounted? „!eject off“ to turn it off.",
    },
    "eject_off": {
        "de": "🛑 Auto-Auswurf ist **aus** — normaler Betrieb, „!go“ zwischen den Drucken. "
              "Erst einschalten, wenn die Farmloop-Tools montiert sind: „!eject on“.",
        "en": "🛑 Auto-eject is **off** — normal operation, „!go“ between prints. Only turn "
              "it on once the Farmloop tools are mounted: „!eject on“.",
    },
    "eject_off_warning": {
        "de": "\n\n⚠️ Diese {n} schon gequeueten Drucke haben den Auswurf **bereits im "
              "Gcode** und werfen trotzdem aus:\n{names}",
        "en": "\n\n⚠️ These {n} already-queued prints have the eject **baked into their "
              "G-code** and will eject anyway:\n{names}",
    },

    # ----- !plate / build plate -----
    "plate_options": {
        "de": "Umstellen mit: !platte cool · textured · smooth · engineering · hot · supertack",
        "en": "Switch with: !plate cool · textured · smooth · engineering · hot · supertack",
    },
    "plate_set": {
        "de": "🛏️ Druckplatte ist jetzt **{bed}** — wird ab dem nächsten Slice eingebacken "
              "(Bett-Temp + Erstschicht-Z). Beim Plattenwechsel hier neu setzen.",
        "en": "🛏️ Build plate is now **{bed}** — baked into every slice from now on "
              "(bed temp + first-layer Z). Set it here again when you swap the plate.",
    },
    "plate_unknown": {
        "de": "🤔 „{arg}“ kenn ich nicht. Aktuell: **{current}**.\n{options}",
        "en": "🤔 I don't know „{arg}“. Currently: **{current}**.\n{options}",
    },
    "plate_status": {
        "de": "🛏️ Aktuelle Druckplatte: **{current}**.\n{options}",
        "en": "🛏️ Current build plate: **{current}**.\n{options}",
    },

    # ----- slice & queue -----
    "slice_all_multi": {
        "de": "🔧 Alle Farben da — ich slice & reihe jetzt {n} Plates ein … (kurz Geduld)",
        "en": "🔧 All colors in — slicing & queuing {n} plates now … (one moment)",
    },
    "slice_one": {
        "de": '🔧 Slice „{label}" für {model} … (kurz Geduld)',
        "en": '🔧 Slicing „{label}" for the {model} … (one moment)',
    },
    "slice_ok_line": {
        "de": '✅ „{label}" — Farben {nums}{note}',
        "en": '✅ „{label}" — colors {nums}{note}',
    },
    "slice_fail_line": {
        "de": '❌ „{label}" fehlgeschlagen',
        "en": '❌ „{label}" failed',
    },
    "slice_tail_queued": {
        "de": "\n{head}Ich sag dir Bescheid, wenn er fertig ist. (!progress · !liste · !abbrechen · !help)",
        "en": "\n{head}I'll let you know when it's done. (!progress · !list · !cancel · !help)",
    },
    "slice_tail_head_multi": {
        "de": "Alles in der Queue! ",
        "en": "Everything's in the queue! ",
    },
    "slice_tail_none": {
        "de": "\nNichts eingereiht. (!help)",
        "en": "\nNothing queued. (!help)",
    },
    "reslice_note_bambu": {
        "de": " · ♻️ für {model} neu geslict",
        "en": " · ♻️ re-sliced for the {model}",
    },
    "reslice_note_sidecar": {
        "de": " · ♻️ über Slicer-Sidecar für {model} geslict",
        "en": " · ♻️ sliced for the {model} via slicer sidecar",
    },
    "reslice_presets_missing": {
        "de": "🚫 Re-Slice nicht möglich (Presets fehlen) — abgebrochen, ich drucke kein "
              "ungeprüftes Fremd-Gcode.",
        "en": "🚫 Re-slice not possible (presets missing) — aborted, I won't print "
              "unverified foreign G-code.",
    },
    "reslice_failed_both": {
        "de": "🚫 Konnte {model}-Gcode nicht erzeugen (Bambuddy + Sidecar gescheitert) — "
              "abgebrochen. Schick mir bitte eine für den {model} geslicte Datei oder ein "
              "{model}-Profil.",
        "en": "🚫 Couldn't produce {model} G-code (Bambuddy + sidecar both failed) — "
              "aborted. Please send me a file sliced for the {model} or a {model} profile.",
    },
    "reslice_exception": {
        "de": "🚫 Re-Slice fehlgeschlagen — abgebrochen (kein Druck mit Fremd-Gcode).",
        "en": "🚫 Re-slice failed — aborted (no printing with foreign G-code).",
    },

    # ----- !skip -----
    "skip_nothing": {
        "de": "Gerade ist keine Farbfrage offen zum Überspringen.",
        "en": "There's no color question open to skip right now.",
    },
    "skip_next": {
        "de": '⏭️ „{label}" übersprungen. ➡️ Nächstes Plate:',
        "en": '⏭️ „{label}" skipped. ➡️ Next plate:',
    },
    "skip_queue_configured": {
        "de": '⏭️ „{label}" übersprungen. Ich reihe die {n} konfigurierten ein:',
        "en": '⏭️ „{label}" skipped. Queuing the {n} configured ones:',
    },
    "skip_nothing_left": {
        "de": "⏭️ Übersprungen — nichts mehr zu drucken übrig.",
        "en": "⏭️ Skipped — nothing left to print.",
    },

    # ----- !cancel -----
    "cancel_discard_rest": {
        "de": '🗑️ Aktuelles/restliche Plates verworfen. Die {n} schon konfigurierten reihe ich ein:',
        "en": '🗑️ Current/remaining plates discarded. Queuing the {n} already configured:',
    },
    "cancel_discarded": {
        "de": '🗑️ Abgebrochen: „{name}" verworfen. Schick mir ein neues Modell — Link oder '
              "Datei —, wenn du willst.",
        "en": '🗑️ Cancelled: „{name}" discarded. Send me a new model — link or file — '
              "whenever you like.",
    },
    "cancel_nothing": {
        "de": "Da ist gerade nichts zum Abbrechen.",
        "en": "There's nothing to cancel right now.",
    },
    "cancel_removed": {
        "de": '🗑️ „{name}" aus der Queue entfernt.',
        "en": '🗑️ „{name}" removed from the queue.',
    },
    "cancel_printing": {
        "de": '🖨️ „{name}" druckt schon — laufende Drucke breche ich nicht ab.',
        "en": '🖨️ „{name}" is already printing — I don\'t cancel running prints.',
    },
    "cancel_not_cancelable": {
        "de": '„{name}" ist nicht mehr abbrechbar (Status: {status}).',
        "en": '„{name}" can no longer be cancelled (status: {status}).',
    },
    "status_unknown": {
        "de": "unbekannt",
        "en": "unknown",
    },

    # ----- !list -----
    "list_empty": {
        "de": "📋 Keine offenen Drucke in der Queue.",
        "en": "📋 No open prints in the queue.",
    },
    "list_eject_tag": {
        "de": " · 🧹 Auswurf",
        "en": " · 🧹 eject",
    },
    "list_noeject_tag": {
        "de": " · ✋ ohne Auswurf",
        "en": " · ✋ no eject",
    },
    "list_header": {
        "de": "📋 Queue (offen):\n",
        "en": "📋 Queue (open):\n",
    },

    # ----- !sync -----
    "sync_in_sync": {
        "de": "🔄 Queue ist synchron — keine fremden Jobs zu übernehmen.",
        "en": "🔄 Queue is in sync — no foreign jobs to adopt.",
    },
    "sync_adopted": {
        "de": "🔄 {n} Job(s) übernommen — ich melde mich, wenn sie fertig sind:\n{lines}",
        "en": "🔄 Adopted {n} job(s) — I'll report when they're done:\n{lines}",
    },

    # ----- !abo -----
    "abo_help": {
        "de": ("🔔 Abo — Benachrichtigungen für Start & Ende eines Drucks.\n"
               "{standing}"
               "🔔 = abonniert, 🔕 = nicht.\n{lines}\n\n"
               "• !abo all (oder !abo on) → alles abonnieren, auch jeden künftigen Druck\n"
               "• !abo 2 3 → nur diese Nummern (einmalig)\n"
               "• !abo off (oder !abo stop / !deabo) → alles abbestellen, "
               "!abo stop 2 → nur Nummer 2"),
        "en": ("🔔 Subscriptions — get notified about a print's start & finish.\n"
               "{standing}"
               "🔔 = subscribed, 🔕 = not.\n{lines}\n\n"
               "• !abo all (or !abo on) → subscribe to everything, future prints included\n"
               "• !abo 2 3 → only these numbers (one-off)\n"
               "• !abo off (or !abo stop / !deabo) → unsubscribe from everything, "
               "!abo stop 2 → only number 2"),
    },
    "abo_help_empty": {
        "de": ("🔔 Keine offenen Drucke in der Queue.\n"
               "{standing}"
               "Mit !abo all abonnierst du alles — auch jeden künftigen Druck. "
               "!abo 2 3 nimmt nur bestimmte Nummern aus !liste."),
        "en": ("🔔 No open prints in the queue.\n"
               "{standing}"
               "!abo all subscribes to everything — future prints included. "
               "!abo 2 3 takes only specific numbers from !list."),
    },
    "abo_standing_on": {
        "de": ("🔔 Abo an — ab jetzt meld ich dir **jeden** Druck: die {n} gerade offenen "
               "und automatisch alle künftigen, egal woher sie kommen.\n"
               "Wieder aus: !abo stop"),
        "en": ("🔔 Subscribed — from now on I'll report **every** print: the {n} open right "
               "now, plus every future one automatically, whatever its source.\n"
               "Turn off: !abo stop"),
    },
    "abo_standing_off": {
        "de": ("🔕 Abo aus — {n} laufende(s) Abo(s) beendet, und künftige Drucke "
               "abonnier ich auch nicht mehr automatisch."),
        "en": ("🔕 Unsubscribed — ended {n} running subscription(s), and I'll no longer "
               "pick up future prints automatically."),
    },
    "abo_standing_state_on": {
        "de": "♾️ Abo: **an für alles** — auch jeder künftige Druck.\n",
        "en": "♾️ Subscription: **on for everything** — every future print too.\n",
    },
    "abo_standing_state_off": {
        "de": "♾️ Abo für alles: aus — !abo all abonniert alles (auch künftige Drucke).\n",
        "en": "♾️ Subscribe-to-everything: off — !abo all covers everything (future prints too).\n",
    },
    "abo_subscribed": {
        "de": ("🔔 {n} Druck(e) abonniert — ich melde Start & Ende:\n{lines}\n"
               "(Nur diese. Für alles inkl. künftiger Drucke: !abo all)"),
        "en": ("🔔 Subscribed to {n} print(s) — I'll report start & finish:\n{lines}\n"
               "(Just these. For everything incl. future prints: !abo all)"),
    },
    "abo_already": {
        "de": "🔔 Schon abonniert — keine neuen Drucke hinzugefügt.",
        "en": "🔔 Already subscribed — nothing new added.",
    },
    "abo_unsubscribed": {
        "de": "🔕 {n} Abo(s) beendet — keine Benachrichtigungen mehr dafür.",
        "en": "🔕 Unsubscribed from {n} print(s) — no more notifications for those.",
    },
    "abo_nothing_subbed": {
        "de": "🔕 Da war kein Abo zum Beenden.",
        "en": "🔕 Nothing was subscribed to unsubscribe from.",
    },
    "abo_bad_pos": {
        "de": "⚠️ Nummer(n) {positions} gibt's nicht — die Queue hat {n} offene(n) Druck(e). (!liste)",
        "en": "⚠️ Number(s) {positions} don't exist — the queue has {n} open print(s). (!list)",
    },

    # ----- !progress -----
    "progress_idle": {
        "de": "🖨️ Drucker ist {state} — gerade kein Druck.",
        "en": "🖨️ Printer is {state} — no print right now.",
    },
    "progress_remaining": {
        "de": "noch ca. {dur}",
        "en": "~{dur} left",
    },
    "progress_done_at": {
        "de": "fertig ~{clock} Uhr",
        "en": "done ~{clock}",
    },
    "progress_paused": {
        "de": "⏸️ pausiert — wartet auf dich",
        "en": "⏸️ paused — waiting for you",
    },
    "progress_hms": {
        "de": "⚠️ Fehler{detail}",
        "en": "⚠️ error{detail}",
    },
    "progress_awaiting_clear": {
        "de": "🧹 Die Platte ist noch voll — schick !go, wenn sie frei ist.",
        "en": "🧹 The plate is still full — send !go once it's clear.",
    },

    # ----- Eingriff nötig (Drucker steht) -----
    "intervention_paused": {
        "de": "⏸️ „{name}\" ist pausiert — der Drucker wartet auf dich.",
        "en": "⏸️ „{name}\" is paused — the printer is waiting for you.",
    },
    "intervention_hms": {
        "de": "⚠️ „{name}\": der Drucker meldet einen Fehler{detail}\n"
              "Bitte schau nach — der Druck steht.",
        "en": "⚠️ „{name}\": the printer reports an error{detail}\n"
              "Please take a look — the print is stopped.",
    },
    "intervention_resolved": {
        "de": "▶️ „{name}\" läuft wieder.",
        "en": "▶️ „{name}\" is running again.",
    },
    "hms_unknown": {
        "de": "unbekannter Fehler",
        "en": "unknown error",
    },

    # ----- !go -----
    "go_failed": {
        "de": "❌ Konnte die Platte nicht freigeben — probier's gleich nochmal.",
        "en": "❌ Couldn't clear the plate — try again in a moment.",
    },
    "go_ok": {
        "de": "✅ Platte als frei bestätigt — der nächste Druck kann starten. 🚀",
        "en": "✅ Plate confirmed clear — the next print can start. 🚀",
    },

    # ----- completion poller -----
    "eta_phrase": {
        "de": " Fertig ca. {clock} Uhr (in ~{dur}).",
        "en": " Done ~{clock} (in ~{dur}).",
    },
    "completion_started": {
        "de": '🖨️ „{name}" druckt jetzt los!{eta} Ich sag Bescheid, wenn er fertig ist. '
              "(!progress für Live-Status + Foto)",
        "en": '🖨️ „{name}" is printing now!{eta} I\'ll let you know when it\'s done. '
              "(!progress for live status + photo)",
    },
    "completion_done_eject": {
        "de": "Auto-Auswurf läuft — der nächste Druck startet von selbst. 🧹",
        "en": "Auto-eject is running — the next print starts on its own. 🧹",
    },
    "completion_done_go": {
        "de": "Wenn die Platte frei ist: !go → nächster Druck startet.",
        "en": "Once the plate is clear: !go → the next print starts.",
    },
    "completion_done": {
        "de": '✅ „{name}" ist fertig gedruckt! 🎉\n{tail}',
        "en": '✅ „{name}" has finished printing! 🎉\n{tail}',
    },
    "completion_failed": {
        "de": '❌ „{name}" ist fehlgeschlagen{detail}\n(!liste zeigt die Queue)',
        "en": '❌ „{name}" failed{detail}\n(!list shows the queue)',
    },

    # ----- !lang -----
    "lang_set": {
        "de": "🌐 Sprache auf Deutsch gestellt. Mit „!english“ wechselst du zu Englisch.",
        "en": "🌐 Language set to English. Send „!deutsch“ to switch back to German.",
    },
    "lang_status": {
        "de": "🌐 Aktuelle Sprache: **Deutsch**. „!english“ → Englisch.",
        "en": "🌐 Current language: **English**. „!deutsch“ → German.",
    },
}


def t(lang, key, **kw):
    """Localized, formatted message for ``key`` in ``lang`` (German fallback)."""
    entry = _M.get(key) or {}
    template = entry.get(normalize(lang)) or entry.get(DEFAULT_LANG) or ""
    return template.format(**kw) if kw else template
