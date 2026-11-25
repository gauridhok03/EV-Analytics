"""Simulated dataset generator (fixed seed => reproducible).

Everything produced here is SIMULATED. Stations are fictional ("VoltHub ...") placed at
approximate real-world coordinates. Deliberately bad session records are added and
labelled in the `injected_issue` column so the validation module can be demonstrated.

Run:  python -m services.data_generator   (writes CSV files into data/)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA_DIR
from services.metrics import compute_cost

TARIFFS = [
    # id, name, price/kWh (INR), peak start, peak end, multiplier
    (1, "Standard Urban", 14.00, 18, 22, 1.20),
    (2, "Premium Urban", 18.00, 17, 22, 1.25),
    (3, "Highway Fast", 22.00, None, None, 1.00),
    (4, "Corporate Campus", 11.00, None, None, 1.00),
    (5, "Off-Peak Saver", 12.50, 9, 17, 0.0 + 1.00),
]

# id, name, city, address, lat, lon, tariff, popularity, kind, n_chargers
STATIONS = [
    ("ST01", "VoltHub Hinjewadi Phase 1", "Pune", "Rajiv Gandhi Infotech Park", 18.5912, 73.7389, 4, 1.3, "campus", 5),
    ("ST02", "VoltHub Kothrud", "Pune", "Karve Road", 18.5074, 73.8077, 1, 1.0, "urban", 4),
    ("ST03", "VoltHub Viman Nagar", "Pune", "Near Phoenix Marketcity", 18.5679, 73.9143, 2, 1.2, "urban", 4),
    ("ST04", "VoltHub Koregaon Park", "Pune", "North Main Road", 18.5362, 73.8940, 2, 1.1, "urban", 3),
    ("ST05", "VoltHub BKC", "Mumbai", "Bandra Kurla Complex", 19.0674, 72.8697, 2, 1.4, "urban", 5),
    ("ST06", "VoltHub Andheri East", "Mumbai", "Chakala, near Airport Road", 19.1136, 72.8697, 1, 1.0, "urban", 4),
    ("ST07", "VoltHub Powai", "Mumbai", "Hiranandani Gardens", 19.1176, 72.9060, 1, 0.9, "urban", 3),
    ("ST08", "VoltHub Lonavala Expressway", "Lonavala", "Mumbai-Pune Expressway Food Plaza", 18.7546, 73.4062, 3, 1.1, "highway", 4),
    ("ST09", "VoltHub Nashik Road", "Nashik", "Near Nashik Road Railway Station", 19.9482, 73.8450, 1, 0.8, "urban", 3),
    ("ST10", "VoltHub Chhatrapati Sambhajinagar", "Chhatrapati Sambhajinagar", "Jalna Road", 19.8762, 75.3433, 5, 0.7, "urban", 3),
    ("ST11", "VoltHub Kolhapur", "Kolhapur", "Tarabai Park", 16.7050, 74.2433, 5, 0.6, "urban", 3),
    ("ST12", "VoltHub Satara Highway", "Satara", "NH48 Highway Plaza", 17.6805, 74.0183, 3, 0.9, "highway", 4),
]

_HOUR_W = np.array([.2, .1, .1, .1, .15, .3, .6, 1.1, 1.6, 1.4, 1.0, .9, 1.0, 1.0, .9, 1.0,
                    1.2, 1.7, 2.0, 1.9, 1.4, .9, .5, .3])
_HOUR_W = _HOUR_W / _HOUR_W.sum()


def _chargers(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for sid, *_rest, kind, n in STATIONS:
        for k in range(1, n + 1):
            if kind == "highway":
                ctype = "DC Ultra-Fast" if k <= 2 else "DC Fast"
            elif kind == "campus":
                ctype = "AC Level 2" if k <= 3 else "DC Fast"
            else:
                ctype = ["AC Level 2", "DC Fast", "DC Fast", "DC Ultra-Fast", "AC Level 2"][k - 1]
            power, conn = {
                "AC Level 2": (float(rng.choice([7.4, 22.0])), "Type 2"),
                "DC Fast": (float(rng.choice([30.0, 50.0, 60.0])), str(rng.choice(["CCS2", "CCS2", "CHAdeMO"]))),
                "DC Ultra-Fast": (float(rng.choice([120.0, 150.0])), "CCS2"),
            }[ctype]
            rows.append({"charger_id": f"{sid}-C{k}", "station_id": sid, "charger_type": ctype,
                         "connector_type": conn, "power_kw": power, "status": "Operational",
                         "is_simulated": True})
    return pd.DataFrame(rows)


def _outages(rng, chargers, w0, w1) -> pd.DataFrame:
    kinds = ["Hardware fault", "Network outage", "Power supply", "Connector damage", "Scheduled maintenance"]
    p = [.28, .22, .18, .12, .20]
    span_h = (w1 - w0).total_seconds() / 3600.0
    rows = []

    def add(cid, sid, start, hours, typ, note):
        end = min(start + pd.Timedelta(hours=hours), w1 - pd.Timedelta(minutes=1))
        if end > start:
            rows.append({"charger_id": cid, "station_id": sid, "start_ts": start.floor("min"),
                         "end_ts": end.floor("min"), "outage_type": typ,
                         "is_planned": typ == "Scheduled maintenance", "description": note,
                         "is_simulated": True})

    mean_h = {"Hardware fault": 1.6, "Network outage": 0.4, "Power supply": 1.1,
              "Connector damage": 2.0, "Scheduled maintenance": 1.5}
    for _ in range(50):
        c = chargers.iloc[int(rng.integers(len(chargers)))]
        typ = str(rng.choice(kinds, p=p))
        start = w0 + pd.Timedelta(hours=float(rng.uniform(0, span_h - 12)))
        hours = float(np.clip(rng.lognormal(mean_h[typ], 0.6), 0.5, 48))
        add(c.charger_id, c.station_id, start, hours, typ, f"Simulated {typ.lower()}")
    # 5 deliberately overlapping outages on the same charger (tests no double counting)
    for r in rng.choice(len(rows), 5, replace=False):
        base = rows[int(r)]
        s = base["start_ts"] + (base["end_ts"] - base["start_ts"]) * 0.5
        add(base["charger_id"], base["station_id"], s, 3.0, "Network outage", "Simulated overlapping event")
    # 4 station-wide power events
    for sid in rng.choice(chargers["station_id"].unique(), 4, replace=False):
        start = w0 + pd.Timedelta(hours=float(rng.uniform(24, span_h - 24)))
        hrs = float(rng.uniform(1.5, 4.0))
        for c in chargers[chargers.station_id == sid].itertuples():
            add(c.charger_id, sid, start, hrs, "Power supply", "Simulated station-wide power cut")
    return pd.DataFrame(rows)


def _clean_sessions(rng, chargers, tariffs, outages, w0, w1) -> pd.DataFrame:
    st = {s[0]: s for s in STATIONS}
    tmap = {t[0]: dict(price_per_kwh=t[2], peak_start_hour=t[3], peak_end_hour=t[4], peak_multiplier=t[5])
            for t in tariffs}
    down: dict[str, list] = {}
    for o in outages.itertuples():
        down.setdefault(o.charger_id, []).append((o.start_ts, o.end_ts))
    base_rate = {"AC Level 2": 0.9, "DC Fast": 2.0, "DC Ultra-Fast": 2.6}
    eff = {"AC Level 2": 0.85, "DC Fast": 0.60, "DC Ultra-Fast": 0.50}
    rows = []
    for c in chargers.itertuples():
        pop = st[c.station_id][7]
        tariff = tmap[st[c.station_id][6]]
        prev_end = w0
        starts = []
        for day in pd.date_range(w0, w1 - pd.Timedelta(days=1), freq="D"):
            n = rng.poisson(base_rate[c.charger_type] * pop * (1.1 if day.dayofweek >= 5 else 1.0))
            for _ in range(n):
                starts.append(day + pd.Timedelta(hours=int(rng.choice(24, p=_HOUR_W)),
                                                 minutes=int(rng.integers(60))))
        for s in sorted(starts):
            s = max(s, prev_end + pd.Timedelta(minutes=5))
            need = float(rng.uniform(6, 30) if c.charger_type == "AC Level 2" else rng.uniform(15, 55))
            dur_h = max(need / (c.power_kw * eff[c.charger_type]), 10 / 60)
            roll = rng.random()
            status = "Completed" if roll < .94 else ("Interrupted" if roll < .98 else "Failed")
            if status == "Interrupted":
                need *= float(rng.uniform(.3, .8)); dur_h *= float(rng.uniform(.4, .8))
            if status == "Failed":
                need, dur_h = 0.0, float(rng.uniform(3, 10)) / 60
            e = s + pd.Timedelta(hours=dur_h)
            if e >= w1:
                continue
            if any(s < de and e > ds for ds, de in down.get(c.charger_id, [])):
                continue
            s, e = s.floor("min"), e.floor("min")
            if e <= s:
                continue
            kwh = round(need, 3)
            rows.append({"charger_id": c.charger_id, "station_id": c.station_id, "start_ts": s, "end_ts": e,
                         "energy_kwh": kwh, "session_status": status,
                         "charging_cost": compute_cost(kwh, s, tariff)})
            prev_end = e
    df = pd.DataFrame(rows).sort_values("start_ts").reset_index(drop=True)
    df.insert(0, "session_id", [f"S{i:06d}" for i in range(1, len(df) + 1)])
    return df


def _inject_issues(rng, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["start_raw"] = None
    df["end_raw"] = None
    df["injected_issue"] = None
    ok = df[df.session_status == "Completed"].index.to_numpy()
    pick = iter(rng.choice(ok, 60, replace=False))
    extra = []
    next_id = len(df) + 1

    for _ in range(8):  # overlap: a second session starting mid-way on the same charger
        i = next(pick); r = df.loc[i]
        s = r.start_ts + (r.end_ts - r.start_ts) * 0.4
        extra.append({**r.to_dict(), "session_id": f"S{next_id:06d}", "start_ts": s.floor("min"),
                      "end_ts": (r.end_ts + pd.Timedelta(minutes=30)).floor("min"),
                      "energy_kwh": round(float(r.energy_kwh) * 0.6, 3),
                      "charging_cost": round(float(r.charging_cost) * 0.6, 2), "injected_issue": "overlap"})
        next_id += 1
    for _ in range(6):
        i = next(pick); df.loc[i, ["start_ts", "injected_issue"]] = [pd.NaT, "missing_start"]
    for _ in range(6):
        i = next(pick); df.loc[i, ["end_ts", "session_status", "injected_issue"]] = [pd.NaT, "In Progress", "missing_end"]
    for _ in range(5):  # duplicate session ID on a different day
        i = next(pick); r = df.loc[i]
        extra.append({**r.to_dict(), "start_ts": r.start_ts + pd.Timedelta(days=3),
                      "end_ts": r.end_ts + pd.Timedelta(days=3), "injected_issue": "duplicate_id"})
    for k in range(4):  # unparseable start text
        i = next(pick)
        df.loc[i, "start_raw"] = ["not-a-time", "2026-13-45 25:61:00", "yesterday evening", "??:??"][k]
        df.loc[i, ["start_ts", "injected_issue"]] = [pd.NaT, "invalid_timestamp"]
    for _ in range(2):  # epoch-zero timestamps
        i = next(pick); df.loc[i, ["end_ts", "injected_issue"]] = [pd.Timestamp("1970-01-01"), "invalid_timestamp"]
    for _ in range(5):
        i = next(pick)
        df.loc[i, ["energy_kwh", "charging_cost", "injected_issue"]] = [-abs(df.loc[i, "energy_kwh"]), 0.0, "negative_energy"]
    for _ in range(5):  # end before start (values swapped)
        i = next(pick); s, e = df.loc[i, "start_ts"], df.loc[i, "end_ts"]
        df.loc[i, ["start_ts", "end_ts", "injected_issue"]] = [e, s, "end_before_start"]
    for _ in range(4):
        i = next(pick); df.loc[i, ["end_ts", "injected_issue"]] = [
            df.loc[i, "start_ts"] + pd.Timedelta(hours=float(rng.uniform(30, 60))), "long_session"]

    df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    df["is_simulated"] = True
    df.insert(0, "record_id", range(1, len(df) + 1))
    return df


def _energy_records(sessions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    clean = sessions[sessions.injected_issue.isna() & (sessions.energy_kwh > 0)]
    for r in clean.itertuples():
        dur = (r.end_ts - r.start_ts) / 3
        part = round(float(r.energy_kwh) / 3, 3)
        last = round(float(r.energy_kwh) - 2 * part, 3)  # exact total
        pw = round(part / (dur.total_seconds() / 3600.0), 1)
        for k in (1, 2, 3):
            rows.append({"session_record_id": r.record_id, "charger_id": r.charger_id,
                         "recorded_at": r.start_ts + dur * k, "energy_kwh": part if k < 3 else last,
                         "power_kw": pw})
    return pd.DataFrame(rows)


def generate_all(seed: int = 42, days: int = 45, end_date=None) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    end_date = pd.Timestamp(end_date) if end_date is not None else pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
    w1 = end_date.normalize() + pd.Timedelta(days=1)
    w0 = w1 - pd.Timedelta(days=days)

    tariffs = pd.DataFrame(TARIFFS, columns=["tariff_id", "tariff_name", "price_per_kwh",
                                             "peak_start_hour", "peak_end_hour", "peak_multiplier"])
    tariffs["currency"] = "INR"
    stations = pd.DataFrame([{
        "station_id": s[0], "station_name": s[1], "city": s[2], "address": s[3], "latitude": s[4],
        "longitude": s[5], "status": "Operational", "tariff_id": s[6],
        "commissioned_on": (w0 - pd.Timedelta(days=int(rng.integers(120, 900)))).date(),
        "is_simulated": True} for s in STATIONS])
    chargers = _chargers(rng)
    outages = _outages(rng, chargers, w0, w1)
    clean = _clean_sessions(rng, chargers, TARIFFS, outages, w0, w1)
    sessions = _inject_issues(rng, clean)
    energy = _energy_records(sessions)
    return {"tariffs": tariffs, "stations": stations, "chargers": chargers,
            "charging_sessions": sessions, "energy_records": energy, "outage_events": outages}


def write_csvs(frames: dict[str, pd.DataFrame], out_dir: Path = DATA_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(out_dir / f"{name}.csv", index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate SIMULATED EV charging datasets")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--end-date", default=None, help="last day of data, YYYY-MM-DD (default: yesterday)")
    a = ap.parse_args()
    frames = generate_all(a.seed, a.days, a.end_date)
    write_csvs(frames)
    for k, v in frames.items():
        print(f"{k:20s} {len(v):6d} rows")
