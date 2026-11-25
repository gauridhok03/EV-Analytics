"""Database connectivity, schema initialisation, CSV loading and query helpers.

CLI:
    python database.py --init           create tables (safe to re-run)
    python database.py --seed           load CSVs from data/ into empty tables
    python database.py --reset          DROP all tables, recreate, then seed
    python database.py --generate       regenerate simulated CSVs first (use with --seed/--reset)
    python database.py --check          test the connection
"""
from __future__ import annotations

import argparse
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

import config

# Load order respects foreign keys; delete order is the reverse.
TABLE_ORDER = ["tariffs", "stations", "chargers", "charging_sessions", "energy_records", "outage_events"]
SERIAL_COLUMNS = {"tariffs": "tariff_id", "charging_sessions": "record_id",
                  "energy_records": "energy_record_id", "outage_events": "outage_id"}

_engine: Optional[Engine] = None


def get_engine(url: Optional[str] = None, fresh: bool = False) -> Engine:
    """Shared engine for the app. Pass url (and fresh=True) to build a separate one, e.g. for tests."""
    global _engine
    if url is not None or fresh:
        return create_engine(url or config.DATABASE_URL, pool_pre_ping=True, future=True)
    if _engine is None:
        _engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)
    return _engine


def test_connection(engine: Optional[Engine] = None) -> tuple[bool, str]:
    try:
        with (engine or get_engine()).connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Connected"
    except Exception as exc:  # surfaced to the UI, never swallowed silently
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


test_connection.__test__ = False  # not a pytest test


def init_schema(engine: Optional[Engine] = None) -> None:
    sql = config.SCHEMA_FILE.read_text(encoding="utf-8")
    with (engine or get_engine()).begin() as conn:
        conn.exec_driver_sql(sql)


def drop_all(engine: Optional[Engine] = None) -> None:
    with (engine or get_engine()).begin() as conn:
        for t in reversed(TABLE_ORDER):
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))


def table_counts(engine: Optional[Engine] = None) -> dict[str, int]:
    eng = engine or get_engine()
    names = set(inspect(eng).get_table_names())
    with eng.connect() as conn:
        return {t: int(conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar())
                for t in TABLE_ORDER if t in names}


def read_sql(query: str, params: Optional[dict] = None, engine: Optional[Engine] = None) -> pd.DataFrame:
    with (engine or get_engine()).connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def execute(query: str, params: Optional[dict] = None, engine: Optional[Engine] = None) -> int:
    """Run a write statement in its own transaction; returns affected row count."""
    with (engine or get_engine()).begin() as conn:
        return conn.execute(text(query), params or {}).rowcount


def _reset_sequence(conn, table: str) -> None:
    col = SERIAL_COLUMNS.get(table)
    if col:
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
            f"COALESCE((SELECT MAX({col}) FROM {table}), 0) + 1, false)"))


def _clean_for_sql(df: pd.DataFrame) -> pd.DataFrame:
    """NaN/NaT -> None so PostgreSQL receives NULL."""
    return df.astype(object).where(df.notna(), None)


def insert_frame(df: pd.DataFrame, table: str, engine: Optional[Engine] = None,
                 conn=None) -> int:
    """Insert rows (only columns that exist in the table). Returns rows inserted."""
    if df.empty:
        return 0
    eng = engine or get_engine()
    cols = [c["name"] for c in inspect(eng).get_columns(table)]
    data = _clean_for_sql(df[[c for c in df.columns if c in cols]])

    def _do(c):
        data.to_sql(table, c, if_exists="append", index=False, method="multi", chunksize=1000)
        _reset_sequence(c, table)

    if conn is not None:
        _do(conn)
    else:
        with eng.begin() as c:
            _do(c)
    return len(data)


def load_sample_data(engine: Optional[Engine] = None, data_dir=None, replace: bool = False) -> dict[str, int]:
    """Load the CSV datasets. Refuses to mix into non-empty tables unless replace=True."""
    eng = engine or get_engine()
    data_dir = data_dir or config.DATA_DIR
    existing = table_counts(eng)
    if any(existing.values()) and not replace:
        raise RuntimeError("Tables already contain data. Use replace=True (or --reset) to overwrite.")
    if replace:
        with eng.begin() as conn:
            for t in reversed(TABLE_ORDER):
                conn.execute(text(f"DELETE FROM {t}"))
    loaded = {}
    with eng.begin() as conn:  # all-or-nothing
        for t in TABLE_ORDER:
            path = data_dir / f"{t}.csv"
            if not path.exists():
                raise FileNotFoundError(f"{path} missing - run: python -m services.data_generator")
            df = pd.read_csv(path)
            for col in df.columns:
                if col.endswith("_ts") or col == "recorded_at":
                    df[col] = pd.to_datetime(df[col], errors="coerce")
            loaded[t] = insert_frame(df, t, eng, conn=conn)
    return loaded


# ---- convenience loaders used by every analytics page ----------------------
def load_stations(engine=None) -> pd.DataFrame:
    return read_sql("""SELECT s.*, t.tariff_name, t.price_per_kwh FROM stations s
                       JOIN tariffs t USING (tariff_id) ORDER BY s.station_id""", engine=engine)


def load_chargers(engine=None) -> pd.DataFrame:
    return read_sql("SELECT * FROM chargers ORDER BY charger_id", engine=engine)


def load_sessions(engine=None) -> pd.DataFrame:
    return read_sql("SELECT * FROM charging_sessions ORDER BY record_id", engine=engine)


def load_outages(engine=None) -> pd.DataFrame:
    return read_sql("SELECT * FROM outage_events ORDER BY outage_id", engine=engine)


def load_tariffs(engine=None) -> pd.DataFrame:
    return read_sql("SELECT * FROM tariffs ORDER BY tariff_id", engine=engine)


def main() -> None:
    ap = argparse.ArgumentParser(description="EV Charging Analytics database tools")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check or not any(vars(a).values()):
        ok, msg = test_connection()
        print(("OK: " if ok else "FAILED: ") + msg)
        if not ok:
            raise SystemExit(1)
    if a.generate:
        from services.data_generator import generate_all, write_csvs
        write_csvs(generate_all())
        print("Generated simulated CSVs in data/")
    if a.reset:
        drop_all()
    if a.init or a.reset or a.seed:
        init_schema()
        print("Schema ready.")
    if a.seed or a.reset:
        print("Loaded:", load_sample_data(replace=True))
    print("Row counts:", table_counts())


if __name__ == "__main__":
    main()
