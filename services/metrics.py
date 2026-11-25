"""Analytics calculations: utilization, reliability, energy, comparison.

Definitions (also shown in the UI):
  Reporting period      [start 00:00, end-date 24:00) naive local time.
  Downtime (charger)    union of its outage intervals inside the period (overlaps merged).
  Available time        period hours - downtime hours.
  Occupied time         union of eligible session intervals inside the period, minus any
                        part that falls inside downtime (overlapping sessions counted once).
  Utilization %         occupied / available * 100.
  Station utilization   sum(occupied) / sum(available) over its chargers (time-weighted).
  Availability %        (period hours - downtime hours) / period hours * 100.
  Station availability  1 - sum(charger downtime) / (n_chargers * period hours).
  Failures              merged outage intervals overlapping the period.
  MTTR                  mean duration of those merged outages.
  MTBF                  total uptime hours / failures (undefined when failures = 0).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from services.intervals import (
    Period, clip_list, merge_intervals, subtract_intervals, total_hours,
)


# --------------------------------------------------------------------------- helpers
def _intervals_by_key(df: pd.DataFrame, key: str, period: Period,
                      end_fill: Optional[pd.Timestamp] = None) -> dict:
    """{key: merged intervals clipped to the period}. NaT end -> end_fill (ongoing)."""
    result: dict = {}
    if df is None or df.empty:
        return result
    for k, g in df.groupby(key):
        pairs = []
        for s, e in zip(g["start_ts"], g["end_ts"]):
            if pd.isna(s):
                continue
            if pd.isna(e):
                if end_fill is None:
                    continue
                e = end_fill
            pairs.append((s, e))
        merged = merge_intervals(clip_list(pairs, period.start, period.end))
        if merged:
            result[k] = merged
    return result


def filter_outages(outages: pd.DataFrame, exclude_planned: bool = False) -> pd.DataFrame:
    if outages is None or outages.empty:
        return pd.DataFrame(columns=["charger_id", "station_id", "start_ts", "end_ts", "is_planned"])
    out = outages.copy()
    if exclude_planned and "is_planned" in out:
        out = out[~out["is_planned"].fillna(False).astype(bool)]
    return out


def sessions_in_period(sessions: pd.DataFrame, period: Period) -> pd.DataFrame:
    """Sessions that START inside the period (used for counts, energy, revenue)."""
    if sessions.empty:
        return sessions
    m = (sessions["start_ts"] >= period.start) & (sessions["start_ts"] < period.end)
    return sessions[m]


# --------------------------------------------------------------------------- utilization
def charger_utilization(chargers: pd.DataFrame, sessions: pd.DataFrame, outages: pd.DataFrame,
                        period: Period, exclude_planned: bool = False) -> pd.DataFrame:
    """One row per charger. `sessions` must already be analytics-eligible."""
    outs = _intervals_by_key(filter_outages(outages, exclude_planned), "charger_id", period,
                             end_fill=period.end)
    ses = _intervals_by_key(sessions.dropna(subset=["start_ts", "end_ts"]), "charger_id", period)
    in_p = sessions_in_period(sessions, period)
    cnt = in_p.groupby("charger_id").size()
    dur = ((in_p["end_ts"] - in_p["start_ts"]).dt.total_seconds() / 60.0).groupby(in_p["charger_id"]).mean() \
        if not in_p.empty else pd.Series(dtype=float)

    rows = []
    for c in chargers.itertuples(index=False):
        down = outs.get(c.charger_id, [])
        occupied = subtract_intervals(ses.get(c.charger_id, []), down)
        down_h, occ_h = total_hours(down), total_hours(occupied)
        avail_h = period.hours - down_h
        rows.append({
            "charger_id": c.charger_id, "station_id": c.station_id,
            "charger_type": getattr(c, "charger_type", None),
            "period_hours": period.hours, "downtime_hours": down_h,
            "available_hours": avail_h, "occupied_hours": occ_h,
            "utilization_pct": (occ_h / avail_h * 100.0) if avail_h > 0 else np.nan,
            "sessions": int(cnt.get(c.charger_id, 0)),
            "avg_duration_min": float(dur.get(c.charger_id, np.nan)),
        })
    return pd.DataFrame(rows)


def station_utilization(charger_df: pd.DataFrame) -> pd.DataFrame:
    g = charger_df.groupby("station_id").agg(
        chargers=("charger_id", "count"), occupied_hours=("occupied_hours", "sum"),
        available_hours=("available_hours", "sum"), downtime_hours=("downtime_hours", "sum"),
        sessions=("sessions", "sum"))
    g["utilization_pct"] = np.where(g["available_hours"] > 0,
                                    g["occupied_hours"] / g["available_hours"] * 100.0, np.nan)
    return g.reset_index()


def _bucket_edges(period: Period, freq: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    pf = {"D": "D", "W": "W-SUN", "M": "M"}[freq]
    periods = pd.period_range(period.start, period.end - pd.Timedelta(microseconds=1), freq=pf)
    return [(max(p.start_time, period.start), min((p + 1).start_time, period.end)) for p in periods]


def utilization_by_bucket(chargers: pd.DataFrame, sessions: pd.DataFrame, outages: pd.DataFrame,
                          period: Period, freq: str = "D", exclude_planned: bool = False) -> pd.DataFrame:
    """Utilization per bucket (D/W/M) and station. Fleet total = group by bucket."""
    if freq not in ("D", "W", "M"):
        raise ValueError("freq must be 'D', 'W' or 'M'")
    outs = _intervals_by_key(filter_outages(outages, exclude_planned), "charger_id", period,
                             end_fill=period.end)
    ses = _intervals_by_key(sessions.dropna(subset=["start_ts", "end_ts"]), "charger_id", period)
    rows = []
    for b0, b1 in _bucket_edges(period, freq):
        hours = (b1 - b0).total_seconds() / 3600.0
        for c in chargers.itertuples(index=False):
            down = clip_list(outs.get(c.charger_id, []), b0, b1)
            occ = subtract_intervals(clip_list(ses.get(c.charger_id, []), b0, b1), down)
            rows.append({"bucket_start": b0, "station_id": c.station_id, "charger_id": c.charger_id,
                         "occupied_hours": total_hours(occ),
                         "available_hours": hours - total_hours(down)})
    df = pd.DataFrame(rows)
    out = df.groupby(["bucket_start", "station_id"], as_index=False)[["occupied_hours", "available_hours"]].sum()
    out["utilization_pct"] = np.where(out["available_hours"] > 0,
                                      out["occupied_hours"] / out["available_hours"] * 100.0, np.nan)
    return out


def fleet_utilization_by_bucket(bucket_df: pd.DataFrame) -> pd.DataFrame:
    g = bucket_df.groupby("bucket_start", as_index=False)[["occupied_hours", "available_hours"]].sum()
    g["utilization_pct"] = np.where(g["available_hours"] > 0,
                                    g["occupied_hours"] / g["available_hours"] * 100.0, np.nan)
    return g


def hourly_profile(sessions: pd.DataFrame, period: Period) -> pd.DataFrame:
    """Peak-hour analysis: session starts and occupied hours by hour of day (0-23).
    Occupied hours use per-charger interval unions, so overlaps are not double-counted."""
    occ = np.zeros(24)
    for ivs in _intervals_by_key(sessions.dropna(subset=["start_ts", "end_ts"]), "charger_id", period).values():
        for s, e in ivs:
            cur = s
            while cur < e:
                nxt = min(cur.floor("h") + pd.Timedelta(hours=1), e)
                occ[cur.hour] += (nxt - cur).total_seconds() / 3600.0
                cur = nxt
    starts = sessions_in_period(sessions, period)["start_ts"].dt.hour.value_counts()
    return pd.DataFrame({"hour": range(24),
                         "session_starts": [int(starts.get(h, 0)) for h in range(24)],
                         "occupied_hours": occ})


# --------------------------------------------------------------------------- reliability
def charger_reliability(chargers: pd.DataFrame, outages: pd.DataFrame, period: Period,
                        exclude_planned: bool = False) -> pd.DataFrame:
    outs = _intervals_by_key(filter_outages(outages, exclude_planned), "charger_id", period,
                             end_fill=period.end)
    rows = []
    for c in chargers.itertuples(index=False):
        ivs = outs.get(c.charger_id, [])
        down_h = total_hours(ivs)
        n = len(ivs)
        up_h = period.hours - down_h
        rows.append({
            "charger_id": c.charger_id, "station_id": c.station_id,
            "outages": n, "downtime_hours": down_h, "uptime_hours": up_h,
            "availability_pct": up_h / period.hours * 100.0,
            "mttr_hours": (down_h / n) if n else np.nan,
            "mtbf_hours": (up_h / n) if n else np.nan,
            "avg_outage_hours": (down_h / n) if n else np.nan,
        })
    return pd.DataFrame(rows)


def station_reliability(charger_rel: pd.DataFrame) -> pd.DataFrame:
    g = charger_rel.groupby("station_id").agg(
        chargers=("charger_id", "count"), outages=("outages", "sum"),
        downtime_hours=("downtime_hours", "sum"), uptime_hours=("uptime_hours", "sum"))
    total = g["downtime_hours"] + g["uptime_hours"]
    g["availability_pct"] = g["uptime_hours"] / total * 100.0
    g["mttr_hours"] = np.where(g["outages"] > 0, g["downtime_hours"] / g["outages"].replace(0, np.nan), np.nan)
    g["mtbf_hours"] = np.where(g["outages"] > 0, g["uptime_hours"] / g["outages"].replace(0, np.nan), np.nan)
    g["avg_outage_hours"] = g["mttr_hours"]
    return g.reset_index()


def reliability_extremes(station_rel: pd.DataFrame) -> dict:
    """Most / least reliable station: highest / lowest availability (ties: fewer / more outages)."""
    if station_rel.empty:
        return {"most": None, "least": None}
    ranked = station_rel.sort_values(["availability_pct", "outages"], ascending=[False, True])
    return {"most": ranked.iloc[0]["station_id"], "least": ranked.iloc[-1]["station_id"]}


# --------------------------------------------------------------------------- energy / cost
def compute_cost(energy_kwh: float, start_ts, tariff: dict) -> float:
    """Charging cost = kWh x price, with the peak multiplier if the session starts in the peak window."""
    price = float(tariff["price_per_kwh"])
    ps, pe = tariff.get("peak_start_hour"), tariff.get("peak_end_hour")
    if ps is not None and pe is not None and not pd.isna(ps) and not pd.isna(pe) and start_ts is not None:
        if int(ps) <= pd.Timestamp(start_ts).hour < int(pe):
            price *= float(tariff.get("peak_multiplier", 1.0))
    return round(float(energy_kwh) * price, 2)


def energy_summary(sessions: pd.DataFrame) -> dict:
    """KPIs for eligible sessions already filtered to the period."""
    n = len(sessions)
    total = float(sessions["energy_kwh"].sum()) if n else 0.0
    return {"sessions": n, "total_energy_kwh": total,
            "avg_energy_per_session_kwh": total / n if n else 0.0,
            "revenue": float(sessions["charging_cost"].fillna(0).sum()) if n else 0.0}


def energy_by_station(sessions: pd.DataFrame) -> pd.DataFrame:
    return sessions.groupby("station_id", as_index=False).agg(
        sessions=("session_id", "count"), energy_kwh=("energy_kwh", "sum"),
        revenue=("charging_cost", "sum"))


def energy_by_bucket(sessions: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    if sessions.empty:
        return pd.DataFrame(columns=["bucket_start", "energy_kwh", "revenue", "sessions"])
    alias = {"D": "D", "W": "W-SUN", "M": "M"}[freq]
    per = sessions["start_ts"].dt.to_period(alias)
    g = sessions.groupby(per).agg(energy_kwh=("energy_kwh", "sum"),
                                  revenue=("charging_cost", "sum"), sessions=("session_id", "count"))
    g.index = g.index.start_time
    return g.rename_axis("bucket_start").reset_index()


# --------------------------------------------------------------------------- comparison
def station_comparison(stations: pd.DataFrame, util: pd.DataFrame, rel: pd.DataFrame,
                       sessions_in: pd.DataFrame) -> pd.DataFrame:
    """One row per station with every comparison metric and 1 = best ranks."""
    en = energy_by_station(sessions_in) if not sessions_in.empty else \
        pd.DataFrame(columns=["station_id", "sessions", "energy_kwh", "revenue"])
    dur = (((sessions_in["end_ts"] - sessions_in["start_ts"]).dt.total_seconds() / 60.0)
           .groupby(sessions_in["station_id"]).mean().rename("avg_duration_min")
           if not sessions_in.empty else pd.Series(dtype=float, name="avg_duration_min"))
    out = stations[["station_id", "station_name", "city"]].merge(
        util[["station_id", "utilization_pct"]], on="station_id", how="left").merge(
        en, on="station_id", how="left").merge(dur, on="station_id", how="left").merge(
        rel[["station_id", "downtime_hours", "availability_pct"]], on="station_id", how="left")
    for col in ("sessions", "energy_kwh", "revenue"):
        out[col] = out[col].fillna(0)
    for col, asc in [("utilization_pct", False), ("energy_kwh", False), ("sessions", False),
                     ("revenue", False), ("availability_pct", False), ("downtime_hours", True)]:
        out[f"rank_{col}"] = out[col].rank(ascending=asc, method="min")
    out["overall_rank_score"] = out[[c for c in out if c.startswith("rank_")]].mean(axis=1)
    out["overall_rank"] = out["overall_rank_score"].rank(method="min").astype(int)
    return out.sort_values("overall_rank").reset_index(drop=True)


# --------------------------------------------------------------------------- alerts / summaries
def generate_alerts(station_rel: pd.DataFrame, stations: pd.DataFrame,
                    low_availability_pct: float, high_downtime_hours: float) -> pd.DataFrame:
    """Low-availability and high-downtime alerts per station (thresholds come from config)."""
    names = stations.set_index("station_id")["station_name"].to_dict()
    rows = []
    for r in station_rel.itertuples():
        if r.availability_pct < low_availability_pct:
            rows.append({"station_id": r.station_id, "station": names.get(r.station_id, r.station_id),
                         "alert": "Low availability", "severity": "High",
                         "detail": f"{r.availability_pct:.2f}% available (threshold {low_availability_pct:g}%)"})
        if r.downtime_hours > high_downtime_hours:
            rows.append({"station_id": r.station_id, "station": names.get(r.station_id, r.station_id),
                         "alert": "High downtime", "severity": "Medium",
                         "detail": f"{r.downtime_hours:.1f} charger-hours down (threshold {high_downtime_hours:g})"})
    return pd.DataFrame(rows, columns=["station_id", "station", "alert", "severity", "detail"])


def daily_summary(sessions_in: pd.DataFrame) -> pd.DataFrame:
    """Date-wise summary of eligible sessions starting in the period."""
    if sessions_in.empty:
        return pd.DataFrame(columns=["date", "sessions", "energy_kwh", "revenue", "avg_duration_min"])
    d = sessions_in.assign(date=sessions_in["start_ts"].dt.date,
                           dur=(sessions_in["end_ts"] - sessions_in["start_ts"]).dt.total_seconds() / 60.0)
    g = d.groupby("date").agg(sessions=("session_id", "count"), energy_kwh=("energy_kwh", "sum"),
                              revenue=("charging_cost", "sum"), avg_duration_min=("dur", "mean"))
    return g.reset_index()
