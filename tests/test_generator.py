"""The simulated dataset meets the brief and the validator catches every injected defect."""
import pandas as pd
import pytest

from services.data_generator import generate_all
from services.validation import validate_sessions

END = "2026-11-23"
NOW = pd.Timestamp("2026-11-24")


@pytest.fixture(scope="module")
def frames():
    return generate_all(seed=42, days=45, end_date=END)


@pytest.fixture(scope="module")
def validated(frames):
    return validate_sessions(frames["charging_sessions"], now=NOW)


def test_dataset_sizes_meet_requirements(frames):
    assert len(frames["stations"]) >= 10
    assert frames["chargers"].groupby("station_id").size().min() >= 3
    assert len(frames["charging_sessions"]) >= 500
    assert len(frames["outage_events"]) >= 50
    assert frames["tariffs"]["price_per_kwh"].nunique() >= 4
    assert frames["chargers"]["charger_type"].nunique() == 3


def test_all_rows_are_labelled_simulated(frames):
    for name in ("stations", "chargers", "charging_sessions", "outage_events"):
        assert frames[name]["is_simulated"].all()


def test_generation_is_reproducible():
    a, b = generate_all(7, 20, END), generate_all(7, 20, END)
    pd.testing.assert_frame_equal(a["charging_sessions"], b["charging_sessions"])


def test_every_injected_defect_type_is_present(frames):
    kinds = set(frames["charging_sessions"]["injected_issue"].dropna())
    assert kinds == {"overlap", "missing_start", "missing_end", "duplicate_id", "invalid_timestamp",
                     "negative_energy", "end_before_start", "long_session"}


EXPECTED = {"overlap": "OVERLAP", "missing_start": "MISSING_START", "missing_end": "MISSING_END",
            "duplicate_id": "DUPLICATE_ID", "invalid_timestamp": "INVALID_TIMESTAMP",
            "negative_energy": "NEGATIVE_ENERGY", "end_before_start": "END_BEFORE_START",
            "long_session": "LONG_SESSION"}


def test_validator_detects_every_injected_record_with_the_right_code(validated):
    inj = validated[validated.injected_issue.notna()]
    assert len(inj) > 0
    for _, r in inj.iterrows():
        assert EXPECTED[r.injected_issue] in r.error_codes, (r.session_id, r.injected_issue, r.error_codes)


def test_clean_rows_are_only_flagged_as_partners_of_injected_defects(validated):
    """A clean-labelled row may be invalid only because it overlaps or shares an ID with an injected row."""
    clean_bad = validated[validated.injected_issue.isna() & ~validated.is_valid]
    assert clean_bad["error_codes"].map(lambda c: set(c) <= {"OVERLAP", "DUPLICATE_ID"}).all()


def test_energy_records_sum_to_session_energy(frames):
    s = frames["charging_sessions"].set_index("record_id")
    sums = frames["energy_records"].groupby("session_record_id")["energy_kwh"].sum()
    assert (sums - s.loc[sums.index, "energy_kwh"]).abs().max() < 1e-6


def test_clean_sessions_never_start_during_an_outage_on_same_charger(frames, validated):
    out = frames["outage_events"]
    clean = validated[validated.injected_issue.isna() & validated.is_valid]
    for cid, g in clean.groupby("charger_id"):
        for o in out[out.charger_id == cid].itertuples():
            hit = ((g.start_ts < o.end_ts) & (g.end_ts > o.start_ts)).sum()
            assert hit == 0
