"""Write operations (CRUD) with input validation. Raises ValueError with a user-readable message."""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import config
import database
from services.metrics import compute_cost

ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,10}$")
CHARGER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,20}$")


def _eng(engine):
    return engine or database.get_engine()


def _one(conn, q, **p):
    return conn.execute(text(q), p).scalar()


def _need(cond: bool, msg: str):
    if not cond:
        raise ValueError(msg)


def _num(v, name, lo=None, hi=None, allow_none=False):
    if v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and pd.isna(v)):
        _need(allow_none, f"{name} is required")
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    _need(lo is None or x >= lo, f"{name} must be at least {lo}")
    _need(hi is None or x <= hi, f"{name} must be at most {hi}")
    return x


# --------------------------------------------------------------------------- stations
def _station_payload(d: dict, conn, creating: bool) -> dict:
    p = {
        "station_id": str(d.get("station_id", "")).strip().upper(),
        "station_name": str(d.get("station_name", "")).strip(),
        "city": str(d.get("city", "")).strip(),
        "address": (str(d.get("address") or "").strip() or None),
        "latitude": _num(d.get("latitude"), "Latitude", -90, 90),
        "longitude": _num(d.get("longitude"), "Longitude", -180, 180),
        "status": d.get("status", "Operational"),
        "tariff_id": int(_num(d.get("tariff_id"), "Tariff")),
        "commissioned_on": d.get("commissioned_on"),
    }
    _need(ID_RE.match(p["station_id"]) is not None, "Station ID must be 2-10 letters, digits, - or _")
    _need(bool(p["station_name"]), "Station name is required")
    _need(bool(p["city"]), "City is required")
    _need(p["status"] in config.STATION_STATUSES, "Invalid station status")
    _need(_one(conn, "SELECT COUNT(*) FROM tariffs WHERE tariff_id=:t", t=p["tariff_id"]) == 1, "Unknown tariff")
    return p


def add_station(d: dict, engine=None) -> str:
    with _eng(engine).begin() as conn:
        p = _station_payload(d, conn, True)
        _need(_one(conn, "SELECT COUNT(*) FROM stations WHERE station_id=:s", s=p["station_id"]) == 0,
              f"Station {p['station_id']} already exists")
        conn.execute(text("""INSERT INTO stations (station_id,station_name,city,address,latitude,longitude,
            status,tariff_id,commissioned_on,is_simulated) VALUES (:station_id,:station_name,:city,:address,
            :latitude,:longitude,:status,:tariff_id,:commissioned_on,FALSE)"""), p)
    return p["station_id"]


def update_station(station_id: str, d: dict, engine=None) -> None:
    with _eng(engine).begin() as conn:
        p = _station_payload({**d, "station_id": station_id}, conn, False)
        n = conn.execute(text("""UPDATE stations SET station_name=:station_name, city=:city, address=:address,
            latitude=:latitude, longitude=:longitude, status=:status, tariff_id=:tariff_id,
            commissioned_on=:commissioned_on WHERE station_id=:station_id"""), p).rowcount
        _need(n == 1, "Station not found")


def station_impact(station_id: str, engine=None) -> dict:
    with _eng(engine).connect() as c:
        return {
            "chargers": _one(c, "SELECT COUNT(*) FROM chargers WHERE station_id=:s", s=station_id),
            "sessions": _one(c, "SELECT COUNT(*) FROM charging_sessions WHERE station_id=:s", s=station_id),
            "outages": _one(c, "SELECT COUNT(*) FROM outage_events WHERE station_id=:s", s=station_id),
        }


def delete_station(station_id: str, engine=None) -> dict:
    """Deletes the station and everything that belongs to it, in ONE transaction (all or nothing).
    Callers must show station_impact() and get explicit confirmation first."""
    impact = station_impact(station_id, engine)
    with _eng(engine).begin() as conn:
        conn.execute(text("DELETE FROM charging_sessions WHERE station_id=:s"), {"s": station_id})  # cascades energy
        conn.execute(text("DELETE FROM outage_events WHERE station_id=:s"), {"s": station_id})
        conn.execute(text("DELETE FROM chargers WHERE station_id=:s"), {"s": station_id})
        n = conn.execute(text("DELETE FROM stations WHERE station_id=:s"), {"s": station_id}).rowcount
        _need(n == 1, "Station not found")
    return impact


# --------------------------------------------------------------------------- chargers
def _charger_payload(d: dict, conn) -> dict:
    p = {"charger_id": str(d.get("charger_id", "")).strip().upper(),
         "station_id": str(d.get("station_id", "")).strip(),
         "charger_type": d.get("charger_type"), "connector_type": d.get("connector_type"),
         "power_kw": _num(d.get("power_kw"), "Power (kW)", 0.1, 1000),
         "status": d.get("status", "Operational")}
    _need(CHARGER_ID_RE.match(p["charger_id"]) is not None, "Charger ID must be 2-20 letters, digits, - or _")
    _need(p["charger_type"] in config.CHARGER_TYPES, "Invalid charger type")
    _need(p["connector_type"] in config.CONNECTOR_TYPES, "Invalid connector type")
    _need(p["status"] in config.CHARGER_STATUSES, "Invalid charger status")
    _need(_one(conn, "SELECT COUNT(*) FROM stations WHERE station_id=:s", s=p["station_id"]) == 1, "Unknown station")
    return p


def add_charger(d: dict, engine=None) -> str:
    with _eng(engine).begin() as conn:
        p = _charger_payload(d, conn)
        _need(_one(conn, "SELECT COUNT(*) FROM chargers WHERE charger_id=:c", c=p["charger_id"]) == 0,
              f"Charger {p['charger_id']} already exists")
        conn.execute(text("""INSERT INTO chargers (charger_id,station_id,charger_type,connector_type,power_kw,status,is_simulated)
            VALUES (:charger_id,:station_id,:charger_type,:connector_type,:power_kw,:status,FALSE)"""), p)
    return p["charger_id"]


def update_charger(charger_id: str, d: dict, engine=None) -> None:
    """The station of an existing charger cannot change (sessions reference the pair)."""
    with _eng(engine).begin() as conn:
        cur = _one(conn, "SELECT station_id FROM chargers WHERE charger_id=:c", c=charger_id)
        _need(cur is not None, "Charger not found")
        p = _charger_payload({**d, "charger_id": charger_id, "station_id": cur}, conn)
        conn.execute(text("""UPDATE chargers SET charger_type=:charger_type, connector_type=:connector_type,
            power_kw=:power_kw, status=:status WHERE charger_id=:charger_id"""), p)


def charger_impact(charger_id: str, engine=None) -> dict:
    with _eng(engine).connect() as c:
        return {"sessions": _one(c, "SELECT COUNT(*) FROM charging_sessions WHERE charger_id=:c", c=charger_id),
                "outages": _one(c, "SELECT COUNT(*) FROM outage_events WHERE charger_id=:c", c=charger_id)}


def delete_charger(charger_id: str, engine=None) -> dict:
    impact = charger_impact(charger_id, engine)
    with _eng(engine).begin() as conn:
        conn.execute(text("DELETE FROM charging_sessions WHERE charger_id=:c"), {"c": charger_id})
        conn.execute(text("DELETE FROM outage_events WHERE charger_id=:c"), {"c": charger_id})
        n = conn.execute(text("DELETE FROM chargers WHERE charger_id=:c"), {"c": charger_id}).rowcount
        _need(n == 1, "Charger not found")
    return impact


# --------------------------------------------------------------------------- sessions
def find_overlaps(conn, charger_id: str, start, end, exclude_record_id: Optional[int] = None) -> list[str]:
    rows = conn.execute(text("""SELECT session_id FROM charging_sessions
        WHERE charger_id=:c AND start_ts IS NOT NULL AND end_ts IS NOT NULL AND start_ts < :e AND end_ts > :s
          AND (:x IS NULL OR record_id <> :x)"""), {"c": charger_id, "s": start, "e": end, "x": exclude_record_id}).all()
    return [r[0] for r in rows]


def session_cost(station_id: str, energy_kwh: float, start_ts, engine=None) -> float:
    with _eng(engine).connect() as c:
        row = c.execute(text("""SELECT t.price_per_kwh, t.peak_start_hour, t.peak_end_hour, t.peak_multiplier
            FROM stations s JOIN tariffs t USING (tariff_id) WHERE s.station_id=:s"""), {"s": station_id}).mappings().first()
    _need(row is not None, "Unknown station")
    return compute_cost(energy_kwh, start_ts, dict(row))


def _session_payload(d: dict, conn, record_id: Optional[int], allow_overlap: bool, allow_duplicate_id: bool) -> dict:
    p = {"session_id": str(d.get("session_id", "")).strip(), "charger_id": d.get("charger_id"),
         "station_id": d.get("station_id"), "start_ts": pd.Timestamp(d["start_ts"]) if d.get("start_ts") is not None else None,
         "end_ts": pd.Timestamp(d["end_ts"]) if d.get("end_ts") is not None else None,
         "energy_kwh": _num(d.get("energy_kwh"), "Energy (kWh)", 0, 2000),
         "session_status": d.get("session_status", "Completed"),
         "charging_cost": _num(d.get("charging_cost"), "Charging cost", 0, allow_none=True)}
    _need(bool(p["session_id"]) and len(p["session_id"]) <= 20, "Session ID is required (max 20 characters)")
    _need(p["session_status"] in config.SESSION_STATUSES, "Invalid session status")
    ok = _one(conn, "SELECT COUNT(*) FROM chargers WHERE charger_id=:c AND station_id=:s",
              c=p["charger_id"], s=p["station_id"])
    _need(ok == 1, "That charger does not belong to the selected station")
    _need(p["start_ts"] is not None, "Start time is required")
    if p["end_ts"] is None:
        _need(p["session_status"] == "In Progress", "End time is required unless the status is 'In Progress'")
    else:
        _need(p["end_ts"] > p["start_ts"], "End time must be after the start time")
        hours = (p["end_ts"] - p["start_ts"]).total_seconds() / 3600
        _need(hours <= config.MAX_SESSION_HOURS, f"Session is {hours:.1f} h long (limit {config.MAX_SESSION_HOURS} h)")
        if not allow_overlap:
            clash = find_overlaps(conn, p["charger_id"], p["start_ts"], p["end_ts"], record_id)
            _need(not clash, "Overlaps existing session(s) on this charger: " + ", ".join(clash[:5]))
    if not allow_duplicate_id:
        dup = _one(conn, "SELECT COUNT(*) FROM charging_sessions WHERE session_id=:i AND (:r IS NULL OR record_id<>:r)",
                   i=p["session_id"], r=record_id)
        _need(dup == 0, f"Session ID {p['session_id']} already exists")
    return p


def _sync_energy_record(conn, record_id: int, p: dict) -> None:
    conn.execute(text("DELETE FROM energy_records WHERE session_record_id=:r"), {"r": record_id})
    if p["end_ts"] is not None and p["energy_kwh"] and p["energy_kwh"] > 0:
        hours = max((p["end_ts"] - p["start_ts"]).total_seconds() / 3600, 1e-6)
        conn.execute(text("""INSERT INTO energy_records (session_record_id,charger_id,recorded_at,energy_kwh,power_kw)
            VALUES (:r,:c,:t,:e,:p)"""), {"r": record_id, "c": p["charger_id"], "t": p["end_ts"],
                                          "e": p["energy_kwh"], "p": round(p["energy_kwh"] / hours, 1)})


def add_session(d: dict, allow_overlap=False, allow_duplicate_id=False, engine=None) -> int:
    with _eng(engine).begin() as conn:
        p = _session_payload(d, conn, None, allow_overlap, allow_duplicate_id)
        rid = conn.execute(text("""INSERT INTO charging_sessions (session_id,charger_id,station_id,start_ts,end_ts,
            energy_kwh,session_status,charging_cost,is_simulated) VALUES (:session_id,:charger_id,:station_id,
            :start_ts,:end_ts,:energy_kwh,:session_status,:charging_cost,FALSE) RETURNING record_id"""), p).scalar()
        _sync_energy_record(conn, rid, p)
    return rid


def update_session(record_id: int, d: dict, allow_overlap=False, allow_duplicate_id=False, engine=None) -> None:
    with _eng(engine).begin() as conn:
        _need(_one(conn, "SELECT COUNT(*) FROM charging_sessions WHERE record_id=:r", r=record_id) == 1, "Record not found")
        p = _session_payload(d, conn, record_id, allow_overlap, allow_duplicate_id)
        conn.execute(text("""UPDATE charging_sessions SET session_id=:session_id, charger_id=:charger_id,
            station_id=:station_id, start_ts=:start_ts, end_ts=:end_ts, start_raw=NULL, end_raw=NULL,
            energy_kwh=:energy_kwh, session_status=:session_status, charging_cost=:charging_cost,
            updated_at=NOW() WHERE record_id=:rid"""), {**p, "rid": record_id})
        _sync_energy_record(conn, record_id, p)


def delete_session(record_id: int, engine=None) -> None:
    with _eng(engine).begin() as conn:
        n = conn.execute(text("DELETE FROM charging_sessions WHERE record_id=:r"), {"r": record_id}).rowcount
        _need(n == 1, "Record not found")


def next_session_id(engine=None) -> str:
    with _eng(engine).connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT session_id FROM charging_sessions WHERE session_id ~ '^S[0-9]+$'")).all()]
    n = max((int(i[1:]) for i in ids), default=0) + 1
    return f"S{n:06d}"
