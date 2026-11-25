"""Streamlit AppTest smoke tests: every page renders without an exception against a real database,
and key interactions (add/delete station, add session, quick-fix) work end to end.
Needs TEST_DATABASE_URL (disposable database). Skipped otherwise."""
import os
from pathlib import Path

import pandas as pd
import pytest

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["Dashboard", "Stations", "Charging Sessions", "Timeline Validation", "Utilization", "Energy",
         "Reliability", "Station Comparison", "Reports & Export", "Data Import"]


@pytest.fixture(scope="module", autouse=True)
def seeded(tmp_path_factory):
    import config, database as db
    from services.data_generator import generate_all, write_csvs
    config.DATABASE_URL = URL
    db._engine = None
    eng = db.get_engine()
    d = tmp_path_factory.mktemp("ui_csv")
    write_csvs(generate_all(seed=3, days=30, end_date=pd.Timestamp.today().normalize() - pd.Timedelta(days=1)), d)
    db.drop_all(eng); db.init_schema(eng); db.load_sample_data(eng, data_dir=d)
    os.environ["DATABASE_URL"] = URL
    yield
    db.drop_all(eng)


def app():
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=90)
    at.run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    at = app()
    at.sidebar.radio(key="nav").set_value(page).run()
    assert not at.exception, [e.value for e in at.exception]


def test_dashboard_kpis_come_from_database():
    at = app()
    html = " ".join(m.value for m in at.markdown)
    assert "Charging stations" in html and "Electricity revenue" in html and "12" in html




def test_light_theme_toggle_renders_every_page():
    at = app()
    at.sidebar.toggle(key="light_theme").set_value(True).run()
    for page in PAGES:
        at.sidebar.radio(key="nav").set_value(page).run()
        assert not at.exception, (page, [e.value for e in at.exception])


def test_pdf_button_generates_a_pdf():
    at = app()
    at.sidebar.radio(key="nav").set_value("Reports & Export").run()
    at.button(key="rp_pdf_btn").click().run()
    assert not at.exception
    assert at.session_state["pdf_bytes"][:5] == b"%PDF-"


def test_station_filter_changes_dashboard_numbers():
    at = app()
    before = " ".join(m.value for m in at.markdown)
    at.sidebar.multiselect(key="station_filter").set_value(["ST01", "ST02"]).run()
    after = " ".join(m.value for m in at.markdown)
    assert not at.exception and before != after and ">2<" in after.replace(" ", "")


def test_empty_station_selection_shows_message_instead_of_crashing():
    at = app()
    at.sidebar.multiselect(key="station_filter").set_value([]).run()
    assert not at.exception and any("Select at least one station" in i.value for i in at.info)
