"""CRUD rules, CSV import and PDF generation against a real PostgreSQL test database."""
import os

import pandas as pd
import pytest

import database as db
from services import importer as I
from services import repository as R
from services.data_generator import generate_all, write_csvs
from services.metrics import generate_alerts
from services.pdf_report import build_pdf_report
from services.validation import validate_sessions, validation_summary

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    e = db.get_engine(URL, fresh=True)
    d = tmp_path_factory.mktemp("svc")
    write_csvs(generate_all(seed=5, days=30, end_date="2026-11-23"), d)
    db.drop_all(e); db.init_schema(e); db.load_sample_data(e, data_dir=d)
    yield e
    db.drop_all(e); e.dispose()


def sess(**kw):
    base = dict(session_id="N000001", charger_id="ST01-C1", station_id="ST01", start_ts=pd.Timestamp("2031-01-01 10:00"),
                end_ts=pd.Timestamp("2031-01-01 11:00"), energy_kwh=10, session_status="Completed", charging_cost=100)
    base.update(kw)
    return base


# ------------------------------------------------------------------ sessions
def test_session_crud_roundtrip(eng):
    rid = R.add_session(sess(), engine=eng)
    row = db.read_sql("SELECT * FROM charging_sessions WHERE record_id=:r", {"r": rid}, eng).iloc[0]
    assert row.energy_kwh == 10 and not row.is_simulated
    assert db.read_sql("SELECT energy_kwh FROM energy_records WHERE session_record_id=:r", {"r": rid}, eng).energy_kwh.sum() == 10
    R.update_session(rid, sess(energy_kwh=25, charging_cost=250), engine=eng)
    assert db.read_sql("SELECT energy_kwh FROM charging_sessions WHERE record_id=:r", {"r": rid}, eng).energy_kwh[0] == 25
    assert db.read_sql("SELECT energy_kwh FROM energy_records WHERE session_record_id=:r", {"r": rid}, eng).energy_kwh.sum() == 25
    R.delete_session(rid, engine=eng)
    assert db.read_sql("SELECT COUNT(*) n FROM charging_sessions WHERE record_id=:r", {"r": rid}, eng).n[0] == 0
    assert db.read_sql("SELECT COUNT(*) n FROM energy_records WHERE session_record_id=:r", {"r": rid}, eng).n[0] == 0


@pytest.mark.parametrize("changes,msg", [
    (dict(end_ts=pd.Timestamp("2031-01-01 09:00")), "after the start"),
    (dict(energy_kwh=-1), "at least 0"),
    (dict(energy_kwh="abc"), "must be a number"),
    (dict(session_status="Bogus"), "Invalid session status"),
    (dict(station_id="ST02"), "does not belong"),
    (dict(end_ts=pd.Timestamp("2031-01-03 12:00")), "limit"),
    (dict(session_id="  "), "Session ID is required"),
    (dict(end_ts=None), "End time is required"),
])
def test_session_input_validation(eng, changes, msg):
    with pytest.raises(ValueError, match=msg):
        R.add_session(sess(**changes), engine=eng)


def test_overlap_and_duplicate_id_are_blocked_unless_allowed(eng):
    rid = R.add_session(sess(), engine=eng)
    with pytest.raises(ValueError, match="Overlaps"):
        R.add_session(sess(session_id="N000002", start_ts=pd.Timestamp("2031-01-01 10:30"), end_ts=pd.Timestamp("2031-01-01 11:30")), engine=eng)
    with pytest.raises(ValueError, match="already exists"):
        R.add_session(sess(start_ts=pd.Timestamp("2031-02-01 10:00"), end_ts=pd.Timestamp("2031-02-01 11:00")), engine=eng)
    r2 = R.add_session(sess(session_id="N000002", start_ts=pd.Timestamp("2031-01-01 10:30"), end_ts=pd.Timestamp("2031-01-01 11:30")),
                       allow_overlap=True, engine=eng)
    v = validate_sessions(db.load_sessions(eng), now=pd.Timestamp("2040-01-01"))
    assert {"OVERLAP"} <= set(sum(v[v.record_id.isin([rid, r2])].error_codes, []))
    R.delete_session(rid, engine=eng); R.delete_session(r2, engine=eng)


def test_correcting_an_invalid_record_makes_it_valid(eng):
    with eng.begin() as c:   # insert a bad record directly (as an import would)
        rid = c.execute(db.text("""INSERT INTO charging_sessions (session_id,charger_id,station_id,start_ts,end_ts,energy_kwh,charging_cost)
            VALUES ('BAD1','ST03-C1','ST03','2031-05-01 12:00','2031-05-01 10:00',-5,0) RETURNING record_id""")).scalar()
    v = validate_sessions(db.load_sessions(eng), now=pd.Timestamp("2040-01-01")).set_index("record_id")
    assert set(v.loc[rid, "error_codes"]) == {"END_BEFORE_START", "NEGATIVE_ENERGY"}
    R.update_session(rid, sess(session_id="BAD1", charger_id="ST03-C1", station_id="ST03", start_ts=pd.Timestamp("2031-05-01 10:00"),
                               end_ts=pd.Timestamp("2031-05-01 12:00"), energy_kwh=5), engine=eng)
    v = validate_sessions(db.load_sessions(eng), now=pd.Timestamp("2040-01-01")).set_index("record_id")
    assert v.loc[rid, "is_valid"]
    R.delete_session(rid, engine=eng)


def test_cost_uses_tariff_peak_multiplier(eng):
    # ST02 uses Standard Urban: 14/kWh, x1.2 from 18:00 to 22:00
    assert R.session_cost("ST02", 10, pd.Timestamp("2031-01-01 10:00"), eng) == 140.0
    assert R.session_cost("ST02", 10, pd.Timestamp("2031-01-01 19:00"), eng) == 168.0


# ------------------------------------------------------------------ stations and chargers
STATION = dict(station_id="TST9", station_name="Test Hub", city="Pune", address="x", latitude=18.5, longitude=73.8,
               status="Operational", tariff_id=1, commissioned_on=pd.Timestamp("2026-01-01").date())


def test_station_validation_rules(eng):
    for bad, msg in [(dict(latitude=95), "at most 90"), (dict(station_id="!"), "Station ID"), (dict(tariff_id=999), "Unknown tariff"),
                     (dict(status="Gone"), "Invalid station status"), (dict(station_name=""), "name is required"),
                     (dict(station_id="ST01"), "already exists")]:
        with pytest.raises(ValueError, match=msg):
            R.add_station({**STATION, **bad}, engine=eng)


def test_station_and_charger_lifecycle_with_safe_delete(eng):
    R.add_station(STATION, engine=eng)
    R.update_station("TST9", {**STATION, "status": "Maintenance", "city": "Mumbai"}, engine=eng)
    s = db.read_sql("SELECT * FROM stations WHERE station_id='TST9'", engine=eng).iloc[0]
    assert s.status == "Maintenance" and s.city == "Mumbai"
    R.add_charger(dict(charger_id="TST9-C1", station_id="TST9", charger_type="DC Fast", connector_type="CCS2", power_kw=50), engine=eng)
    with pytest.raises(ValueError, match="already exists"):
        R.add_charger(dict(charger_id="TST9-C1", station_id="TST9", charger_type="DC Fast", connector_type="CCS2", power_kw=50), engine=eng)
    with pytest.raises(ValueError, match="Unknown station"):
        R.add_charger(dict(charger_id="ZZ-C1", station_id="NOPE", charger_type="DC Fast", connector_type="CCS2", power_kw=50), engine=eng)
    R.add_session(sess(charger_id="TST9-C1", station_id="TST9"), engine=eng)
    imp = R.station_impact("TST9", eng)
    assert imp == {"chargers": 1, "sessions": 1, "outages": 0}
    done = R.delete_station("TST9", eng)
    assert done == imp
    assert db.read_sql("SELECT COUNT(*) n FROM stations WHERE station_id='TST9'", engine=eng).n[0] == 0
    assert db.read_sql("SELECT COUNT(*) n FROM charging_sessions WHERE station_id='TST9'", engine=eng).n[0] == 0


def test_deleting_unknown_station_changes_nothing(eng):
    before = db.table_counts(eng)
    with pytest.raises(ValueError):
        R.delete_station("NOPE", eng)
    assert db.table_counts(eng) == before


# ------------------------------------------------------------------ CSV import
def test_import_rejects_unstorable_rows_and_keeps_bad_timelines(eng):
    df = pd.DataFrame({
        "session_id": ["I1", "I2", "I3", "I4"], "charger_id": ["ST01-C1", "ST01-C1", "NOPE-C1", "ST01-C1"],
        "station_id": ["ST01", "ST01", "NOPE", "ST01"],
        "start_ts": ["2032-01-01 10:00", "garbage", "2032-01-01 10:00", "2032-01-02 10:00"],
        "end_ts": ["2032-01-01 11:00", "2032-01-01 11:00", "2032-01-01 11:00", "2032-01-02 09:00"],
        "energy_kwh": [5, 5, 5, -5], "session_status": ["Completed"] * 3 + ["Bogus"]})
    ok, bad, _ = I.prepare_import("charging_sessions", df, eng)
    assert list(ok.session_id) == ["I1", "I2"]                      # garbage timestamp is kept for review
    assert dict(zip(bad.session_id, bad.reject_reason)) == {
        "I3": "charger_id does not belong to station_id (or does not exist)", "I4": "invalid session_status"}
    assert I.commit_import("charging_sessions", ok, eng) == 2
    row = db.read_sql("SELECT start_ts, start_raw FROM charging_sessions WHERE session_id='I2'", engine=eng).iloc[0]
    assert pd.isna(row.start_ts) and row.start_raw == "garbage"
    with eng.begin() as c:
        c.execute(db.text("DELETE FROM charging_sessions WHERE session_id IN ('I1','I2')"))


def test_import_missing_columns_is_an_error(eng):
    with pytest.raises(ValueError, match="Missing required"):
        I.prepare_import("stations", pd.DataFrame({"station_id": ["X1"]}), eng)


@pytest.mark.parametrize("table", list(I.REQUIRED))
def test_downloaded_template_is_accepted_or_rejected_only_for_data_reasons(table, eng):
    df = pd.read_csv(pd.io.common.StringIO(I.template_csv(table)))
    ok, bad, _ = I.prepare_import(table, df, eng)
    assert len(ok) + len(bad) == 1


def test_station_import_roundtrip(eng):
    df = pd.DataFrame([{**STATION, "station_id": "IMP1", "commissioned_on": "2026-01-01"}, {**STATION, "station_id": "IMP2", "latitude": 999}])
    ok, bad, _ = I.prepare_import("stations", df, eng)
    assert list(ok.station_id) == ["IMP1"] and list(bad.reject_reason) == ["invalid latitude"]
    I.commit_import("stations", ok, eng)
    assert R.station_impact("IMP1", eng)["chargers"] == 0
    R.delete_station("IMP1", eng)


# ------------------------------------------------------------------ alerts and PDF
def test_alerts_use_thresholds():
    rel = pd.DataFrame({"station_id": ["A", "B"], "availability_pct": [90.0, 99.0], "downtime_hours": [10.0, 100.0]})
    st_ = pd.DataFrame({"station_id": ["A", "B"], "station_name": ["Alpha", "Beta"]})
    a = generate_alerts(rel, st_, 95, 48)
    assert set(zip(a.station_id, a.alert)) == {("A", "Low availability"), ("B", "High downtime")}


def test_pdf_report_is_a_valid_pdf(eng):
    comp = pd.DataFrame({"overall_rank": [1], "station_id": ["ST01"], "station_name": ["Alpha"], "utilization_pct": [12.0],
                         "availability_pct": [99.0], "sessions": [100], "energy_kwh": [2000.0], "revenue": [30000.0], "downtime_hours": [3.0]})
    pdf = build_pdf_report("2026-01-01 to 2026-01-31", {"Sessions": "100"}, comp, pd.DataFrame(columns=["station_id", "station", "alert", "severity", "detail"]),
                           validation_summary(validate_sessions(db.load_sessions(eng))), {"most": "ST01", "least": "ST01"},
                           {"valid": 90, "invalid": 10, "valid_pct": 90.0, "total": 100})
    assert pdf[:5] == b"%PDF-" and len(pdf) > 2000
