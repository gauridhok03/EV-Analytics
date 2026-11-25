"""Central configuration for the EV Charging Analytics platform."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
SQL_DIR = BASE_DIR / "sql"
SCHEMA_FILE = SQL_DIR / "schema.sql"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/ev_analytics",
)
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

APP_TITLE = "EV Charging Analytics"
DATA_DISCLAIMER = (
    "SIMULATED DATA: all stations, sessions, outages and tariffs in this "
    "database were generated for demonstration and testing."
)

# ---- Validation thresholds -------------------------------------------------
MAX_SESSION_HOURS = 24          # sessions longer than this are flagged for review
MIN_VALID_YEAR = 2000           # timestamps before Jan 1 of this year are invalid
FUTURE_TOLERANCE_DAYS = 1       # timestamps further in the future are invalid

# ---- Alert thresholds ------------------------------------------------------
LOW_AVAILABILITY_PCT = 95.0
HIGH_DOWNTIME_HOURS = 48.0      # per station, charger-hours within the period

# ---- Domain enumerations (kept in sync with sql/schema.sql) -----------------
STATION_STATUSES = ["Operational", "Maintenance", "Offline"]
CHARGER_STATUSES = ["Operational", "Maintenance", "Offline"]
CHARGER_TYPES = ["AC Level 2", "DC Fast", "DC Ultra-Fast"]
CONNECTOR_TYPES = ["Type 2", "CCS2", "CHAdeMO", "GB/T"]
SESSION_STATUSES = ["Completed", "Interrupted", "In Progress", "Failed"]
OUTAGE_TYPES = [
    "Hardware fault",
    "Network outage",
    "Power supply",
    "Connector damage",
    "Scheduled maintenance",
]

# ---- Theme -----------------------------------------------------------------
THEMES = {
    "dark": {
        "bg": "#0B1B2E", "panel": "#12263D", "text": "#E8EEF5",
        "muted": "#8FA3B8", "accent": "#3DDC97", "border": "#1F3752",
        "plotly_template": "plotly_dark",
    },
    "light": {
        "bg": "#F5F7FA", "panel": "#FFFFFF", "text": "#0B1B2E",
        "muted": "#5B6B7D", "accent": "#12A66B", "border": "#DCE3EA",
        "plotly_template": "plotly_white",
    },
}
