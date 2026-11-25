"""CSV import with row-level checks. Rows that cannot be stored (unknown charger, bad enum, existing key...)
are returned in `rejected` with the reason, never dropped silently. Session rows with timeline problems
ARE imported (the validation module reports them) because the schema can store them."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text

import config
import database
from services.validation import normalize_session_frame

REQUIRED = {
    "tariffs": ["tariff_name", "price_per_kwh"],
    "stations": ["station_id", "station_name", "city", "latitude", "longitude", "tariff_id"],
    "chargers": ["charger_id", "station_id", "charger_type", "connector_type", "power_kw"],
    "charging_sessions": ["session_id", "charger_id", "station_id", "start_ts", "end_ts", "energy_kwh"],
    "outage_events": ["charger_id", "station_id", "start_ts", "outage_type"],
}
TEMPLATES = {
    "tariffs": {"tariff_name": "My Tariff", "price_per_kwh": 15.0, "peak_start_hour": 18, "peak_end_hour": 22, "peak_multiplier": 1.2},
    "stations": {"station_id": "ST99", "station_name": "Demo Station", "city": "Pune", "address": "", "latitude": 18.52,
                 "longitude": 73.85, "status": "Operational", "tariff_id": 1},
    "chargers": {"charger_id": "ST99-C1", "station_id": "ST99", "charger_type": "DC Fast", "connector_type": "CCS2",
                 "power_kw": 50, "status": "Operational"},
    "charging_sessions": {"session_id": "S900001", "charger_id": "ST01-C1", "station_id": "ST01",
                          "start_ts": "2026-11-01 10:00", "end_ts": "2026-11-01 11:00", "energy_kwh": 12.5,
                          "session_status": "Completed", "charging_cost": 175.0},
    "outage_events": {"charger_id": "ST01-C1", "station_id": "ST01", "start_ts": "2026-11-01 02:00",
                      "end_ts": "2026-11-01 04:00", "outage_type": "Hardware fault", "is_planned": False, "description": ""},
}


def template_csv(table: str) -> str:
    return pd.DataFrame([TEMPLATES[table]]).to_csv(index=False)


def _keys(conn, q):
    return {tuple(r) for r in conn.execute(text(q)).all()}


def prepare_import(table: str, df: pd.DataFrame, engine=None):
    """Returns (accepted_df, rejected_df[with 'reject_reason'], messages)."""
    if table not in REQUIRED:
        raise ValueError(f"Import is not supported for {table}")
    missing = [c for c in REQUIRED[table] if c not in df.columns]
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing))
    d = df.copy().reset_index(drop=True)
    reason = pd.Series("", index=d.index, dtype=object)

    def bad(mask, msg):
        m = pd.Series(mask, index=d.index).fillna(False).astype(bool) & (reason == "")
        reason[m] = msg

    eng = engine or database.get_engine()
    with eng.connect() as conn:
        pairs = _keys(conn, "SELECT charger_id, station_id FROM chargers")
        if table == "tariffs":
            d["price_per_kwh"] = pd.to_numeric(d["price_per_kwh"], errors="coerce")
            bad(d["price_per_kwh"].isna() | (d["price_per_kwh"] < 0), "price_per_kwh must be a non-negative number")
            existing = {r[0] for r in conn.execute(text("SELECT tariff_name FROM tariffs"))}
            bad(d["tariff_name"].isin(existing) | d["tariff_name"].duplicated(), "tariff name already exists")
        elif table == "stations":
            for c in ("latitude", "longitude"):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            bad(d["latitude"].isna() | ~d["latitude"].between(-90, 90), "invalid latitude")
            bad(d["longitude"].isna() | ~d["longitude"].between(-180, 180), "invalid longitude")
            if "status" in d:
                bad(~d["status"].fillna("Operational").isin(config.STATION_STATUSES), "invalid status")
            tariffs = {r[0] for r in conn.execute(text("SELECT tariff_id FROM tariffs"))}
            bad(~pd.to_numeric(d["tariff_id"], errors="coerce").isin(list(tariffs)), "unknown tariff_id")
            existing = {r[0] for r in conn.execute(text("SELECT station_id FROM stations"))}
            bad(d["station_id"].isin(existing) | d["station_id"].duplicated(), "station_id already exists")
        elif table == "chargers":
            stations = {r[0] for r in conn.execute(text("SELECT station_id FROM stations"))}
            d["power_kw"] = pd.to_numeric(d["power_kw"], errors="coerce")
            bad(~d["station_id"].isin(stations), "unknown station_id")
            bad(~d["charger_type"].isin(config.CHARGER_TYPES), "invalid charger_type")
            bad(~d["connector_type"].isin(config.CONNECTOR_TYPES), "invalid connector_type")
            bad(d["power_kw"].isna() | (d["power_kw"] <= 0), "power_kw must be > 0")
            existing = {r[0] for r in conn.execute(text("SELECT charger_id FROM chargers"))}
            bad(d["charger_id"].isin(existing) | d["charger_id"].duplicated(), "charger_id already exists")
        elif table == "charging_sessions":
            d = normalize_session_frame(d)
            d["energy_kwh"] = pd.to_numeric(d["energy_kwh"], errors="coerce")
            if "session_status" not in d:
                d["session_status"] = "Completed"
            d["session_status"] = d["session_status"].fillna("Completed")
            bad([(c, s) not in pairs for c, s in zip(d["charger_id"], d["station_id"])],
                "charger_id does not belong to station_id (or does not exist)")
            bad(~d["session_status"].isin(config.SESSION_STATUSES), "invalid session_status")
            bad(d["session_id"].isna() | (d["session_id"].astype(str).str.len() > 20), "session_id missing or longer than 20 chars")
            d["is_simulated"] = False
        elif table == "outage_events":
            d = normalize_session_frame(d.rename(columns={}))  # parses start_ts/end_ts the same way
            bad([(c, s) not in pairs for c, s in zip(d["charger_id"], d["station_id"])],
                "charger_id does not belong to station_id (or does not exist)")
            bad(~d["outage_type"].isin(config.OUTAGE_TYPES), "invalid outage_type")
            bad(d["start_ts"].isna(), "start_ts missing or unparseable")
            bad(d["end_ts"].notna() & (d["end_ts"] <= d["start_ts"]), "end_ts must be after start_ts")
            if "is_planned" in d:
                d["is_planned"] = d["is_planned"].map(lambda v: str(v).strip().lower() in ("true", "1", "yes"))
            d = d.drop(columns=[c for c in ("start_raw", "end_raw", "energy_kwh") if c in d])
            d["is_simulated"] = False
        if table in ("stations", "chargers", "tariffs"):
            d["is_simulated"] = False if table != "tariffs" else d.get("is_simulated")
    rejected = d[reason != ""].copy()
    rejected["reject_reason"] = reason[reason != ""]
    accepted = d[reason == ""].copy()
    if table == "tariffs":
        accepted = accepted.drop(columns=["is_simulated"], errors="ignore")
    msgs = [f"{len(accepted)} row(s) ready to import, {len(rejected)} rejected."]
    return accepted, rejected, msgs


def commit_import(table: str, accepted: pd.DataFrame, engine=None) -> int:
    drop = [c for c in ("record_id", "outage_id", "created_at", "updated_at", "injected_issue") if c in accepted]
    return database.insert_frame(accepted.drop(columns=drop), table, engine or database.get_engine())
