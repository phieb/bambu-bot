"""Environment-driven configuration for bambu-bot."""
import os

BAMBUDDY_URL = os.environ.get("BAMBUDDY_URL", "http://bambuddy:8010")
# OrcaSlicer/Bambu-Studio sidecar (the real slicer) — called directly to slice
# files Bambuddy's own slice rejects (e.g. multi-plate 3mf with off-bed objects).
SLICER_URL = os.environ.get("SLICER_URL", "http://192.168.178.116:3001")
SIGNAL_URL = os.environ.get("SIGNAL_URL", "http://signal-api:8080")
BOT_NUMBER = os.environ.get("SIGNAL_BOT_NUMBER", "")
DB_PATH = os.environ.get("DB_PATH", "/data/bambu.db")
PRINTER_ID = int(os.environ.get("BAMBUDDY_PRINTER_ID", "1"))
GROUP_NAME = os.environ.get("BAMBU_GROUP_NAME", "🖨️ Bambu Print Queue")
# Target machine for re-slicing MakerWorld imports (they often arrive sliced for
# X1C). Short code as it appears in slicer preset names (@BBL <model>).
PRINTER_MODEL = os.environ.get("BAMBUDDY_PRINTER_MODEL", "P1S")
NOZZLE_DIAMETER = os.environ.get("BAMBUDDY_NOZZLE", "0.4")
# Bed size (mm, square) — used to center raw STLs before slicing. P1S/X1 = 256.
BED_SIZE_MM = float(os.environ.get("BAMBUDDY_BED_SIZE_MM", "256"))
# Library folder that Signal-uploaded files land in (created if missing).
SIGNAL_FOLDER_NAME = os.environ.get("BAMBU_SIGNAL_FOLDER", "signal")
# Thingiverse app token (thingiverse.com/developers). Empty → Thingiverse links
# get the generic "send me the file" reply instead of a direct download.
THINGIVERSE_TOKEN = os.environ.get("THINGIVERSE_TOKEN", "")
# Auto-eject (Farmloop): max print height (mm) that is safe while the eject tools
# are mounted — above it the descending bed runs into the bender clip mid-print.
# Conservative default; verify at the machine (the eject bends around Z190–250).
EJECT_MAX_HEIGHT_MM = float(os.environ.get("EJECT_MAX_HEIGHT_MM", "180"))
# Build plate physically installed on the printer. Baked into every re-slice as
# the slicer's ``curr_bed_type`` so the bed temperature and first-layer Z-offset
# match the real plate — the P1S does NOT report its mounted plate over Bambuddy,
# so this can't be auto-detected; set it to whatever is on the bed. Canonical
# BambuStudio/OrcaSlicer values: 'Cool Plate', 'Engineering Plate', 'High Temp
# Plate', 'Textured PEI Plate', 'Smooth PEI Plate', 'Cool Plate (SuperTack)',
# 'Supertack Plate'. (Without this the slice inherits the preset default —
# 'Textured PEI Plate' — and runs e.g. PLA at 55/65 °C on a 35 °C Cool Plate.)
BED_TYPE = os.environ.get("BAMBUDDY_BED_TYPE", "Cool Plate")
