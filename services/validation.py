"""Session timeline validation.

validate_sessions() never drops rows: it annotates each row with
  error_codes    list of machine-readable codes
  error_messages human-readable text with the exact reason
  is_valid       True when no rule was violated
so the UI can show valid and invalid records separately and let users fix them.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from config import FUTURE_TOLERANCE_DAYS, MAX_SESSION_HOURS, MIN_VALID_YEAR

ERROR_LABELS = {
    "MISSING_START": "Missing start event",
    "MISSING_END": "Missing end event",
    "END_BEFORE_START": "End time is not after start time",
    "DUPLICATE_ID": "Duplicate session ID",
    "NEGATIVE_ENERGY": "Negative energy value",
    "INVALID_TIMESTAMP": "Invalid timestamp",
    "LONG_SESSION": "Unusually long session",
    "OVERLAP": "Overlaps another session on the same charger",
}
ERROR_CODES = list(ERROR_LABELS)

_NULL_TEXT = {"", "nan", "nat", "none", "null", "<na>"}


def normalize_session_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Parse start/end timestamps. Unparseable text is moved to start_raw/end_raw
    and the timestamp becomes NaT, so the original input is never lost."""
    out = df.copy()
    for side in ("start", "end"):
        ts, raw = f"{side}_ts", f"{side}_raw"
        if ts not in out:
            out[ts] = pd.NaT
        if raw not in out:
            out[raw] = None
        out[raw] = out[raw].astype(object).where(out[raw].notna(), None)
        col = out[ts]
        if not pd.api.types.is_datetime64_any_dtype(col):
            txt = col.astype(object).map(
                lambda v: None if v is None or (isinstance(v, float) and np.isnan(v))
                or str(v).strip().lower() in _NULL_TEXT else str(v).strip()
            )
            parsed = pd.to_datetime(txt, errors="coerce", format="mixed")
            bad = parsed.isna() & txt.notna()
            out[raw] = [t if (b and r is None) else r for t, b, r in zip(txt, bad, out[raw])]
            col = parsed
        if getattr(col.dt, "tz", None) is not None:
            col = col.dt.tz_localize(None)
        out[ts] = col
    if "energy_kwh" in out:
        out["energy_kwh"] = pd.to_numeric(out["energy_kwh"], errors="coerce")
    return out


def validate_sessions(
    df: pd.DataFrame,
    max_hours: float = MAX_SESSION_HOURS,
    now: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Return a copy of df annotated with error_codes, error_messages and is_valid."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now()
    min_date = pd.Timestamp(year=MIN_VALID_YEAR, month=1, day=1)
    max_date = now + pd.Timedelta(days=FUTURE_TOLERANCE_DAYS)

    d = normalize_session_frame(df).reset_index(drop=True)
    n = len(d)
    codes: list[list[str]] = [[] for _ in range(n)]
    extra: list[dict[str, str]] = [dict() for _ in range(n)]  # code -> detail text

    def flag(mask, code, detail=None):
        for i in np.flatnonzero(np.asarray(mask)):
            if code not in codes[i]:
                codes[i].append(code)
            if detail is not None:
                extra[i][code] = detail(i) if callable(detail) else detail

    s, e = d["start_ts"], d["end_ts"]
    s_raw, e_raw = d["start_raw"].notna(), d["end_raw"].notna()

    s_out = s.notna() & ((s < min_date) | (s > max_date))
    e_out = e.notna() & ((e < min_date) | (e > max_date))
    s_bad = s.isna() & s_raw
    e_bad = e.isna() & e_raw

    flag(s.isna() & ~s_raw, "MISSING_START")
    in_progress = (d.get("session_status", pd.Series("", index=d.index)) == "In Progress") \
        & s.notna() & (s >= now - pd.Timedelta(hours=max_hours))
    flag(e.isna() & ~e_raw & ~in_progress, "MISSING_END")

    flag(s_bad | s_out | e_bad | e_out, "INVALID_TIMESTAMP", lambda i: "; ".join(filter(None, [
        f"start '{d.at[i, 'start_raw']}' is not a valid time" if s_bad[i] else
        (f"start {s[i]} is outside the plausible range" if s_out[i] else ""),
        f"end '{d.at[i, 'end_raw']}' is not a valid time" if e_bad[i] else
        (f"end {e[i]} is outside the plausible range" if e_out[i] else ""),
    ])))

    usable = s.notna() & e.notna() & ~s_out & ~e_out
    flag(usable & (e <= s), "END_BEFORE_START",
         lambda i: f"start {s[i]} but end {e[i]}")

    if "energy_kwh" in d:
        flag(d["energy_kwh"] < 0, "NEGATIVE_ENERGY", lambda i: f"energy = {d.at[i, 'energy_kwh']} kWh")

    if "session_id" in d:
        dup = d["session_id"].notna() & d["session_id"].duplicated(keep=False)
        counts = d["session_id"].map(d["session_id"].value_counts())
        flag(dup, "DUPLICATE_ID", lambda i: f"ID '{d.at[i, 'session_id']}' appears {counts[i]} times")

    dur_h = (e - s).dt.total_seconds() / 3600.0
    flag(usable & (e > s) & (dur_h > max_hours), "LONG_SESSION",
         lambda i: f"duration {dur_h[i]:.1f} h exceeds {max_hours:g} h")

    # --- overlaps on the same charger (only rows with a coherent interval) ---
    timeline_ok = usable & (e > s)
    overlaps: dict[int, list[str]] = {}
    for _, g in d[timeline_ok].groupby("charger_id"):
        g = g.sort_values("start_ts")
        idx = g.index.to_numpy()
        starts, ends = g["start_ts"].to_numpy(), g["end_ts"].to_numpy()
        ids = g["session_id"].astype(str).to_numpy()
        for a in range(len(g)):
            b = a + 1
            while b < len(g) and starts[b] < ends[a]:   # sorted by start -> early exit
                overlaps.setdefault(int(idx[a]), []).append(ids[b])
                overlaps.setdefault(int(idx[b]), []).append(ids[a])
                b += 1
    for i, others in overlaps.items():
        shown = ", ".join(sorted(set(others))[:3])
        more = len(set(others)) - 3
        codes[i].append("OVERLAP")
        extra[i]["OVERLAP"] = f"overlaps {shown}" + (f" (+{more} more)" if more > 0 else "")

    d["error_codes"] = codes
    d["error_messages"] = [
        "; ".join(f"{ERROR_LABELS[c]}" + (f" ({extra[i][c]})" if c in extra[i] and extra[i][c] else "")
                  for c in codes[i])
        for i in range(n)
    ]
    d["is_valid"] = [len(c) == 0 for c in codes]
    return d


def split_valid_invalid(validated: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return validated[validated["is_valid"]].copy(), validated[~validated["is_valid"]].copy()


def validation_summary(validated: pd.DataFrame) -> pd.DataFrame:
    """Row per error code with the number of affected records."""
    rows = [{"code": c, "rule": ERROR_LABELS[c],
             "records": int(sum(c in x for x in validated["error_codes"]))} for c in ERROR_CODES]
    return pd.DataFrame(rows)


def analytics_eligible(validated: pd.DataFrame, include_overlaps: bool = True) -> pd.DataFrame:
    """Sessions that may feed analytics.

    Valid rows are always eligible. With include_overlaps=True, rows whose ONLY issue
    is OVERLAP are kept too: they have a coherent interval and the interval-union
    logic in services.metrics prevents double-counting occupied time. All other
    error types are excluded because their times or energy cannot be trusted.
    """
    ok = validated["is_valid"]
    if include_overlaps:
        ok = ok | validated["error_codes"].map(lambda c: c == ["OVERLAP"])
    return validated[ok].copy()
