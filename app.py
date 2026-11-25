"""EV Charging Station Utilization and Reliability Analytics Platform - entry point.
Run with:  streamlit run app.py
"""
import importlib

import streamlit as st

import config
import database as db
from modules import common

st.set_page_config(page_title=config.APP_TITLE, page_icon=":zap:", layout="wide", initial_sidebar_state="expanded")

PAGES = {
    "Dashboard": ("dashboard", True),
    "Stations": ("station_management", False),
    "Charging Sessions": ("session_management", False),
    "Timeline Validation": ("timeline_validation", True),
    "Utilization": ("utilization_analytics", True),
    "Energy": ("energy_analytics", True),
    "Reliability": ("reliability_analysis", True),
    "Station Comparison": ("station_comparison", True),
    "Reports & Export": ("reports", True),
    "Data Import": ("data_import", False),
}


def bootstrap_screen(message: str):
    st.markdown("## EV Charging Analytics")
    st.error(message)
    return


def main():
    common.apply_native_theme()
    common.inject_css()
    st.sidebar.markdown('<div class="brand"><span>\u26a1</span> EV Charging Analytics</div>'
                        '<div class="brand-sub">Utilization &amp; reliability platform</div>', unsafe_allow_html=True)
    ok, msg = db.test_connection()
    if not ok:
        bootstrap_screen(f"Cannot connect to PostgreSQL: {msg}")
        st.markdown("Check that PostgreSQL is running and that `DATABASE_URL` in your `.env` file is correct "
                    "(see README, section 2). Then reload this page.")
        st.stop()

    counts = db.table_counts()
    if not counts or counts.get("stations", 0) == 0:
        st.markdown("## EV Charging Analytics")
        st.info("The database is empty. Create the tables and load the simulated sample dataset to begin.")
        if st.button("Create tables and load simulated data", type="primary"):
            with st.spinner("Loading..."):
                db.init_schema()
                if not (config.DATA_DIR / "stations.csv").exists():
                    from services.data_generator import generate_all, write_csvs
                    write_csvs(generate_all())
                db.load_sample_data(replace=True)
            common.refresh()
            st.rerun()
        st.stop()

    page = st.sidebar.radio("Navigation", list(PAGES), key="nav", label_visibility="collapsed")
    st.sidebar.toggle("Light theme", key="light_theme", help="Switches charts, cards and (best effort) widgets.")
    data = common.load_data()
    ctx = common.sidebar_filters(data) if PAGES[page][1] else None
    if PAGES[page][1] and ctx is None:
        st.stop()
    st.sidebar.caption(config.DATA_DISCLAIMER)
    module = importlib.import_module(f"modules.{PAGES[page][0]}")
    module.render(ctx, data) if PAGES[page][1] else module.render(data)


main()
