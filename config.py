"""Environment-driven configuration for bambu-bot."""
import os

BAMBUDDY_URL = os.environ.get("BAMBUDDY_URL", "http://bambuddy:8010")
SIGNAL_URL = os.environ.get("SIGNAL_URL", "http://signal-api:8080")
BOT_NUMBER = os.environ.get("SIGNAL_BOT_NUMBER", "")
DB_PATH = os.environ.get("DB_PATH", "/data/bambu.db")
PRINTER_ID = int(os.environ.get("BAMBUDDY_PRINTER_ID", "1"))
GROUP_NAME = os.environ.get("BAMBU_GROUP_NAME", "🖨️ Bambu Print Queue")
# Target machine for re-slicing MakerWorld imports (they often arrive sliced for
# X1C). Short code as it appears in slicer preset names (@BBL <model>).
PRINTER_MODEL = os.environ.get("BAMBUDDY_PRINTER_MODEL", "P1S")
NOZZLE_DIAMETER = os.environ.get("BAMBUDDY_NOZZLE", "0.4")
# Library folder that Signal-uploaded files land in (created if missing).
SIGNAL_FOLDER_NAME = os.environ.get("BAMBU_SIGNAL_FOLDER", "signal")
