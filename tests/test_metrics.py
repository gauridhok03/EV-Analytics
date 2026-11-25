"""Utilization, availability, MTBF/MTTR, energy and cost calculations with hand-computed answers."""
import numpy as np
import pandas as pd
import pytest

from services.intervals import make_period, merge_intervals, subtract_intervals
from services.metrics import (
    charger_reliability, charger_utilization, compute_cost, energy_by_bucket, energy_summary,
    hourly_profile, station_reliability, station_utilization, utilization_by_bucket,
)

T = pd.Timestamp
DAY = make_period("2026-01-01", "2026-01-01")  # exactly 24 h
CHARGERS = pd.DataFrame({"charger_id": ["C1", "C2"], "station_id": ["S1", "S1"], "charger_type": ["DC Fast"] * 2})


def sessions(*items):
    return pd.DataFrame([{"session_id": f"S{i}", "charger_id": c, "station_id": "S1",
                          "start_ts": T(s), "end_ts": T(e), "energy_kwh": k, "charging_cost": k * 10}
                         for i, (c, s, e, k) in enumerate(items)])


def outages(*items):
    return pd.DataFrame([{"charger_id": c, "station_id": "S1", "start_ts": T(s),
                          "end_ts": T(e) if e else pd.NaT, "is_planned": False} for c, s, e in items],
                        columns=["charger_id", "station_id", "start_ts", "end_ts", "is_planned"])


NO_OUT = outages()


# ---------------------------------------------------------------- interval helpers
def test_merge_intervals_merges_overlapping_and_touching():
    m = merge_intervals([(T("2026-01-01 08:00"), T("2026-01-01 10:00")),
                         (T("2026-01-01 09:00"), T("2026-01-01 11:00")),
                         (T("2026-01-01 11:00"), T("2026-01-01 12:00")),
                         (T("2026-01-01 14:00"), T("2026-01-01 15:00"))])
    assert m == [(T("2026-01-01 08:00"), T("2026-01-01 12:00")), (T("2026-01-01 14:00"), T("2026-01-01 15:00"))]


def test_subtract_intervals():
    base = [(T("2026-01-01 08:00"), T("2026-01-01 12:00"))]
    cut = [(T("2026-01-01 09:00"), T("2026-01-01 10:00")), (T("2026-01-01 11:00"), T("2026-01-01 13:00"))]
    assert subtract_intervals(base, cut) == [(T("2026-01-01 08:00"), T("2026-01-01 09:00")),
                                             (T("2026-01-01 10:00"), T("2026-01-01 11:00"))]


# ---------------------------------------------------------------- utilization
def test_simple_utilization():
    s = sessions(("C1", "2026-01-01 08:00", "2026-01-01 10:00", 20))
    r = charger_utilization(CHARGERS, s, NO_OUT, DAY).set_index("charger_id")
    assert r.loc["C1", "utilization_pct"] == pytest.approx(2 / 24 * 100)       # 8.3333 %
    assert r.loc["C2", "utilization_pct"] == 0


def test_overlapping_sessions_are_not_double_counted():
    s = sessions(("C1", "2026-01-01 08:00", "2026-01-01 10:00", 20), ("C1", "2026-01-01 09:00", "2026-01-01 11:00", 20))
    r = charger_utilization(CHARGERS, s, NO_OUT, DAY).set_index("charger_id")
    assert r.loc["C1", "occupied_hours"] == pytest.approx(3.0)                  # union 08-11, not 4 h
    assert r.loc["C1", "sessions"] == 2


def test_utilization_excludes_outage_time_from_both_sides():
    s = sessions(("C1", "2026-01-01 08:00", "2026-01-01 10:00", 20), ("C1", "2026-01-01 09:00", "2026-01-01 11:00", 20))
    o = outages(("C1", "2026-01-01 10:00", "2026-01-01 12:00"))
    r = charger_utilization(CHARGERS, s, o, DAY).set_index("charger_id")
    assert r.loc["C1", "occupied_hours"] == pytest.approx(2.0)                  # 08-10; 10-11 was down
    assert r.loc["C1", "available_hours"] == pytest.approx(22.0)
    assert r.loc["C1", "utilization_pct"] == pytest.approx(2 / 22 * 100)        # 9.0909 %


def test_sessions_are_clipped_to_the_reporting_period():
    s = sessions(("C1", "2025-12-31 23:00", "2026-01-01 01:00", 10))
    r = charger_utilization(CHARGERS, s, NO_OUT, DAY).set_index("charger_id")
    assert r.loc["C1", "occupied_hours"] == pytest.approx(1.0)


def test_station_utilization_is_time_weighted():
    s = sessions(("C1", "2026-01-01 00:00", "2026-01-01 12:00", 50))
    st = station_utilization(charger_utilization(CHARGERS, s, NO_OUT, DAY)).iloc[0]
    assert st["utilization_pct"] == pytest.approx(12 / 48 * 100)                # 25 %


def test_daily_buckets_split_a_session_across_midnight():
    p = make_period("2026-01-01", "2026-01-02")
    s = sessions(("C1", "2026-01-01 23:00", "2026-01-02 01:00", 10))
    b = utilization_by_bucket(CHARGERS, s, NO_OUT, p, "D").groupby("bucket_start")["occupied_hours"].sum()
    assert list(b.round(6)) == [1.0, 1.0]


def test_weekly_and_monthly_buckets_cover_the_period():
    p = make_period("2026-01-01", "2026-02-10")
    s = sessions(("C1", "2026-01-15 08:00", "2026-01-15 10:00", 10))
    wk = utilization_by_bucket(CHARGERS, s, NO_OUT, p, "W")
    mo = utilization_by_bucket(CHARGERS, s, NO_OUT, p, "M")
    assert wk.groupby("station_id")["available_hours"].sum().iloc[0] == pytest.approx(p.hours * 2)
    assert mo["bucket_start"].nunique() == 2 and mo["occupied_hours"].sum() == pytest.approx(2.0)


def test_hourly_profile_peak_hour():
    s = sessions(("C1", "2026-01-01 18:30", "2026-01-01 19:30", 10))
    h = hourly_profile(s, DAY).set_index("hour")
    assert h.loc[18, "occupied_hours"] == pytest.approx(0.5) and h.loc[19, "occupied_hours"] == pytest.approx(0.5)
    assert h.loc[18, "session_starts"] == 1


# ---------------------------------------------------------------- reliability
def test_overlapping_outages_counted_once():
    o = outages(("C1", "2026-01-01 10:00", "2026-01-01 12:00"), ("C1", "2026-01-01 11:00", "2026-01-01 13:00"))
    r = charger_reliability(CHARGERS, o, DAY).set_index("charger_id").loc["C1"]
    assert r.downtime_hours == pytest.approx(3.0) and r.outages == 1
    assert r.availability_pct == pytest.approx(21 / 24 * 100)                   # 87.5 %
    assert r.mttr_hours == pytest.approx(3.0) and r.mtbf_hours == pytest.approx(21.0)


def test_mtbf_and_mttr_with_two_separate_outages():
    o = outages(("C1", "2026-01-01 02:00", "2026-01-01 03:00"), ("C1", "2026-01-01 20:00", "2026-01-01 21:00"))
    r = charger_reliability(CHARGERS, o, DAY).set_index("charger_id").loc["C1"]
    assert r.outages == 2 and r.mttr_hours == pytest.approx(1.0) and r.mtbf_hours == pytest.approx(11.0)


def test_no_failures_gives_undefined_mtbf_and_full_availability():
    r = charger_reliability(CHARGERS, NO_OUT, DAY).set_index("charger_id").loc["C1"]
    assert r.availability_pct == 100 and np.isnan(r.mtbf_hours) and np.isnan(r.mttr_hours)


def test_ongoing_outage_runs_to_period_end():
    o = outages(("C1", "2026-01-01 18:00", None))
    r = charger_reliability(CHARGERS, o, DAY).set_index("charger_id").loc["C1"]
    assert r.downtime_hours == pytest.approx(6.0)


def test_outage_started_before_period_is_clipped():
    o = outages(("C1", "2025-12-31 22:00", "2026-01-01 02:00"))
    r = charger_reliability(CHARGERS, o, DAY).set_index("charger_id").loc["C1"]
    assert r.downtime_hours == pytest.approx(2.0)


def test_station_availability_aggregates_charger_hours():
    o = outages(("C1", "2026-01-01 10:00", "2026-01-01 12:00"), ("C1", "2026-01-01 11:00", "2026-01-01 13:00"))
    st = station_reliability(charger_reliability(CHARGERS, o, DAY)).iloc[0]
    assert st.availability_pct == pytest.approx((24 + 21) / 48 * 100)           # 93.75 %
    assert st.downtime_hours == pytest.approx(3.0)


def test_planned_outages_can_be_excluded():
    o = outages(("C1", "2026-01-01 10:00", "2026-01-01 12:00"))
    o["is_planned"] = True
    r = charger_reliability(CHARGERS, o, DAY, exclude_planned=True).set_index("charger_id").loc["C1"]
    assert r.downtime_hours == 0


# ---------------------------------------------------------------- energy and cost
def test_energy_summary_and_buckets():
    s = sessions(("C1", "2026-01-01 08:00", "2026-01-01 09:00", 20), ("C2", "2026-01-02 08:00", "2026-01-02 09:00", 40))
    e = energy_summary(s)
    assert e["total_energy_kwh"] == 60 and e["avg_energy_per_session_kwh"] == 30 and e["revenue"] == 600
    d = energy_by_bucket(s, "D")
    assert list(d["energy_kwh"]) == [20, 40]


def test_cost_flat_and_peak_tariff():
    tariff = {"price_per_kwh": 14, "peak_start_hour": 18, "peak_end_hour": 22, "peak_multiplier": 1.2}
    assert compute_cost(10, T("2026-01-01 10:00"), tariff) == 140.0
    assert compute_cost(10, T("2026-01-01 19:00"), tariff) == 168.0           # 10 x 14 x 1.2
    assert compute_cost(10, T("2026-01-01 22:00"), tariff) == 140.0           # window is [18, 22)
    assert compute_cost(10, T("2026-01-01 19:00"), {"price_per_kwh": 22, "peak_start_hour": None,
                                                    "peak_end_hour": None}) == 220.0
