"""PostgreSQL integration tests: connection, schema, constraints, CSV import, CRUD.

Needs a database. Set TEST_DATABASE_URL (a DEDICATED, disposable database: the tables are dropped!):
    export TEST_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/ev_analytics_test
Without it these tests are skipped.
"""
import os

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import database as db
from services.data_generator import generate_all
from services.validation import validate_sessions

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    eng = db.get_engine(URL, fresh=True)
    ok, msg = db.test_connection(eng)
    if not ok:
        pytest.skip(f"database unreachable: {msg}")
    from services.data_generator import write_csvs
    d = tmp_path_factory.mktemp("csv")
    write_csvs(generate_all(seed=1, days=30, end_date="2026-11-23"), d)
    db.drop_all(eng)
    db.init_schema(eng)
    db.load_sample_data(eng, data_dir=d)
    yield eng
    db.drop_all(eng)
    eng.dispose()


@pytest.fixture
def conn(engine):
    """Every test runs in a transaction that is rolled back, so tests cannot affect each other."""
    c = engine.connect()
    t = c.begin()
    yield c
    t.rollback()
    c.close()


def one(conn, q, **p):
    return conn.execute(text(q), p).scalar()


NEW = dict(sid="T000001", ch="ST01-C1", st="ST01")


def insert_session(conn, **over):
    p = {**NEW, "s": "2026-01-01 10:00", "e": "2026-01-01 11:00", "kwh": 12.5, "status": "Completed", **over}
    return conn.execute(text("""INSERT INTO charging_sessions
        (session_id, charger_id, station_id, start_ts, end_ts, energy_kwh, session_status, charging_cost)
        VALUES (:sid, :ch, :st, :s, :e, :kwh, :status, 175) RETURNING record_id"""), p).scalar()


def test_connection(engine):
    assert db.test_connection(engine) == (True, "Connected")


def test_bad_url_reports_failure_instead_of_raising():
    ok, msg = db.test_connection(db.get_engine("postgresql+psycopg2://x:y@localhost:1/nope", fresh=True))
    assert ok is False and msg


def test_init_schema_is_idempotent(engine):
    db.init_schema(engine)
    db.init_schema(engine)


def test_csv_import_row_counts_and_labels(engine):
    counts = db.table_counts(engine)
    assert counts["stations"] >= 10 and counts["charging_sessions"] >= 500 and counts["outage_events"] >= 50
    assert counts["tariffs"] == 5 and counts["chargers"] > counts["stations"]
    assert db.read_sql("SELECT COUNT(*) n FROM charging_sessions WHERE is_simulated", engine=engine).n[0] == counts["charging_sessions"]


def test_seeding_non_empty_tables_requires_replace(engine):
    with pytest.raises(RuntimeError):
        db.load_sample_data(engine)


def test_identity_sequence_continues_after_csv_import(conn):
    rid = insert_session(conn)
    assert rid > one(conn, "SELECT COUNT(*) FROM charging_sessions") - 1


def test_session_crud(conn):
    rid = insert_session(conn)
    assert one(conn, "SELECT energy_kwh FROM charging_sessions WHERE record_id=:r", r=rid) == 12.5
    conn.execute(text("UPDATE charging_sessions SET energy_kwh=20 WHERE record_id=:r"), {"r": rid})
    assert one(conn, "SELECT energy_kwh FROM charging_sessions WHERE record_id=:r", r=rid) == 20
    conn.execute(text("DELETE FROM charging_sessions WHERE record_id=:r"), {"r": rid})
    assert one(conn, "SELECT COUNT(*) FROM charging_sessions WHERE record_id=:r", r=rid) == 0


def test_station_charger_outage_crud(conn):
    conn.execute(text("""INSERT INTO stations (station_id,station_name,city,latitude,longitude,tariff_id)
                         VALUES ('TST1','Test','Pune',18.5,73.8,1)"""))
    conn.execute(text("""INSERT INTO chargers (charger_id,station_id,charger_type,connector_type,power_kw)
                         VALUES ('TST1-C1','TST1','DC Fast','CCS2',50)"""))
    conn.execute(text("""INSERT INTO outage_events (charger_id,station_id,start_ts,end_ts,outage_type)
                         VALUES ('TST1-C1','TST1','2026-01-01 01:00','2026-01-01 02:00','Hardware fault')"""))
    conn.execute(text("UPDATE stations SET status='Maintenance' WHERE station_id='TST1'"))
    assert one(conn, "SELECT status FROM stations WHERE station_id='TST1'") == "Maintenance"
    with pytest.raises(IntegrityError):   # cannot delete a station that still has chargers
        with conn.begin_nested():
            conn.execute(text("DELETE FROM stations WHERE station_id='TST1'"))


def test_duplicate_session_ids_and_negative_energy_are_storable(conn):
    insert_session(conn)
    insert_session(conn, kwh=-4)
    assert one(conn, "SELECT COUNT(*) FROM charging_sessions WHERE session_id='T000001'") == 2


def test_missing_timestamps_are_storable(conn):
    rid = insert_session(conn, s=None, e=None)
    assert one(conn, "SELECT start_ts IS NULL AND end_ts IS NULL FROM charging_sessions WHERE record_id=:r", r=rid)


def test_charger_must_belong_to_station(conn):
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            insert_session(conn, ch="ST01-C1", st="ST02")


def test_check_constraints_reject_bad_values(conn):
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            insert_session(conn, status="Bogus")
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            conn.execute(text("""INSERT INTO outage_events (charger_id,station_id,start_ts,end_ts,outage_type)
                VALUES ('ST01-C1','ST01','2026-01-01 05:00','2026-01-01 04:00','Hardware fault')"""))
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            conn.execute(text("""INSERT INTO stations (station_id,station_name,city,latitude,longitude,tariff_id)
                                 VALUES ('BAD','x','y',123,0,1)"""))


def test_deleting_session_cascades_to_energy_records(conn):
    rid = one(conn, "SELECT session_record_id FROM energy_records LIMIT 1")
    assert one(conn, "SELECT COUNT(*) FROM energy_records WHERE session_record_id=:r", r=rid) > 0
    conn.execute(text("DELETE FROM charging_sessions WHERE record_id=:r"), {"r": rid})
    assert one(conn, "SELECT COUNT(*) FROM energy_records WHERE session_record_id=:r", r=rid) == 0


def test_user_csv_with_unparseable_timestamp_round_trips_through_database(engine):
    from services.validation import normalize_session_frame
    raw = pd.DataFrame({"session_id": ["U1"], "charger_id": ["ST01-C1"], "station_id": ["ST01"],
                        "start_ts": ["garbage"], "end_ts": ["2026-01-01 10:00"], "energy_kwh": [5.0],
                        "session_status": ["Completed"], "charging_cost": [70.0]})
    with engine.begin() as c:
        db.insert_frame(normalize_session_frame(raw), "charging_sessions", engine, conn=c)
        row = pd.read_sql(text("SELECT * FROM charging_sessions WHERE session_id='U1'"), c)
        assert row.start_ts.isna().iloc[0] and row.start_raw.iloc[0] == "garbage"
        v = validate_sessions(row, now=pd.Timestamp("2026-11-24"))
        assert "INVALID_TIMESTAMP" in v.error_codes.iloc[0]
        c.execute(text("DELETE FROM charging_sessions WHERE session_id='U1'"))


def test_validation_runs_on_database_data(engine):
    v = validate_sessions(db.load_sessions(engine), now=pd.Timestamp("2026-11-24"))
    assert (~v.is_valid).sum() >= 50 and v.is_valid.sum() > 0 and len(v) == db.table_counts(engine)["charging_sessions"]
