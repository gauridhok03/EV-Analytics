"""Timeline validation tests. `now` is fixed so results are deterministic."""
import pandas as pd
import pytest

from services.validation import (
    analytics_eligible, split_valid_invalid, validate_sessions, validation_summary,
)

NOW = pd.Timestamp("2026-06-01 12:00")


def row(sid, charger="C1", start="2026-05-01 08:00", end="2026-05-01 09:00", kwh=10.0,
        status="Completed", **kw):
    return {"session_id": sid, "charger_id": charger, "station_id": "S1", "start_ts": start,
            "end_ts": end, "energy_kwh": kwh, "session_status": status, **kw}


def run(*rows):
    return validate_sessions(pd.DataFrame(rows), now=NOW)


def codes(v, sid):
    return v.loc[v.session_id == sid, "error_codes"].iloc[0]


def test_valid_session_has_no_errors():
    v = run(row("A"))
    assert v.is_valid.all() and codes(v, "A") == [] and v.error_messages.iloc[0] == ""


def test_overlap_flags_both_sessions_with_partner_id():
    v = run(row("A", start="2026-05-01 08:00", end="2026-05-01 10:00"),
            row("B", start="2026-05-01 09:00", end="2026-05-01 11:00"))
    assert codes(v, "A") == ["OVERLAP"] and codes(v, "B") == ["OVERLAP"]
    assert "overlaps B" in v.set_index("session_id").loc["A", "error_messages"]


def test_back_to_back_sessions_do_not_overlap():
    v = run(row("A", end="2026-05-01 09:00"), row("B", start="2026-05-01 09:00", end="2026-05-01 10:00"))
    assert v.is_valid.all()


def test_overlap_only_within_same_charger():
    v = run(row("A", charger="C1"), row("B", charger="C2"))
    assert v.is_valid.all()


def test_missing_start_and_missing_end():
    v = run(row("A", start=None), row("B", charger="C2", end=None))
    assert codes(v, "A") == ["MISSING_START"] and codes(v, "B") == ["MISSING_END"]


def test_recent_in_progress_session_is_not_missing_end():
    v = run(row("A", start="2026-06-01 10:00", end=None, status="In Progress"))
    assert v.is_valid.all()


def test_old_in_progress_session_is_missing_end():
    v = run(row("A", start="2026-05-01 10:00", end=None, status="In Progress"))
    assert codes(v, "A") == ["MISSING_END"]


def test_end_before_start():
    v = run(row("A", start="2026-05-01 10:00", end="2026-05-01 09:00"))
    assert codes(v, "A") == ["END_BEFORE_START"]


def test_zero_duration_counts_as_end_not_after_start():
    v = run(row("A", start="2026-05-01 10:00", end="2026-05-01 10:00"))
    assert codes(v, "A") == ["END_BEFORE_START"]


def test_duplicate_session_ids_flag_every_copy():
    v = run(row("A"), row("A", start="2026-05-02 08:00", end="2026-05-02 09:00"), row("B", charger="C2"))
    assert v.loc[v.session_id == "A", "error_codes"].map(lambda c: "DUPLICATE_ID" in c).all()
    assert codes(v, "B") == []


def test_negative_energy():
    v = run(row("A", kwh=-3.5))
    assert codes(v, "A") == ["NEGATIVE_ENERGY"] and "-3.5" in v.error_messages.iloc[0]


def test_unparseable_text_timestamp_is_kept_in_raw_and_flagged():
    v = run(row("A", start="not-a-time"))
    assert codes(v, "A") == ["INVALID_TIMESTAMP"]
    assert v.start_raw.iloc[0] == "not-a-time"           # original input preserved
    assert "MISSING_START" not in codes(v, "A")


def test_out_of_range_timestamps():
    v = run(row("A", end="1970-01-01 00:00"), row("B", charger="C2", start="2099-01-01 00:00", end="2099-01-01 01:00"))
    assert "INVALID_TIMESTAMP" in codes(v, "A") and "INVALID_TIMESTAMP" in codes(v, "B")


def test_unusually_long_session():
    v = run(row("A", start="2026-05-01 00:00", end="2026-05-03 06:00"))
    assert codes(v, "A") == ["LONG_SESSION"]


def test_multiple_errors_are_all_reported():
    v = run(row("A", start="2026-05-01 10:00", end="2026-05-01 09:00", kwh=-1))
    assert set(codes(v, "A")) == {"END_BEFORE_START", "NEGATIVE_ENERGY"}


def test_invalid_rows_are_never_dropped():
    v = run(row("A"), row("B", charger="C2", start=None), row("C", charger="C3", kwh=-1))
    valid, invalid = split_valid_invalid(v)
    assert len(valid) + len(invalid) == 3 and set(invalid.session_id) == {"B", "C"}


def test_summary_counts():
    v = run(row("A", start=None), row("B", charger="C2", kwh=-1))
    s = validation_summary(v).set_index("code")["records"]
    assert s["MISSING_START"] == 1 and s["NEGATIVE_ENERGY"] == 1 and s["OVERLAP"] == 0


def test_analytics_eligibility_overlap_toggle():
    v = run(row("A", start="2026-05-01 08:00", end="2026-05-01 10:00"),
            row("B", start="2026-05-01 09:00", end="2026-05-01 11:00"),
            row("C", charger="C2", kwh=-1))
    assert set(analytics_eligible(v, include_overlaps=True).session_id) == {"A", "B"}
    assert analytics_eligible(v, include_overlaps=False).empty
