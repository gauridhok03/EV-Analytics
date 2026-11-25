"""Data import through CSV, templates and sample-data reset."""
import pandas as pd
import streamlit as st

import database as db
from modules import common as C
from services import importer as I
from services.validation import validate_sessions, validation_summary

LABELS = {"charging_sessions": "Charging sessions", "stations": "Stations", "chargers": "Chargers",
          "outage_events": "Outage events", "tariffs": "Tariffs"}


def render(data: dict):
    C.page_header("Data Import", "Load your own CSV files. Rows that cannot be stored are reported with a reason, never dropped silently.", simulated=False)
    t1, t2 = st.tabs(["Import CSV", "Database"])
    with t1:
        table = st.selectbox("Table", list(LABELS), format_func=LABELS.get, key="imp_table")
        st.caption("Required columns: " + ", ".join(I.REQUIRED[table]) + ". Import order for new data: tariffs, stations, chargers, then sessions/outages.")
        st.download_button("Download CSV template", I.template_csv(table).encode(), f"{table}_template.csv", "text/csv", key=f"imp_tpl_{table}")
        up = st.file_uploader("CSV file", type="csv", key=f"imp_file_{table}")
        if up is not None:
            try:
                df = pd.read_csv(up)
                ok, bad, msgs = I.prepare_import(table, df)
            except Exception as e:
                st.error(f"Could not read this file: {e}")
                return
            st.info(" ".join(msgs))
            if table == "charging_sessions" and len(ok):
                v = validate_sessions(ok)
                n_bad = int((~v.is_valid).sum())
                st.markdown(f"**Timeline pre-check:** {n_bad} of {len(v)} rows have validation issues. They will still be imported so you can correct them under Timeline Validation.")
                C.show_df(validation_summary(v).query("records > 0"))
            if len(bad):
                st.warning(f"{len(bad)} row(s) rejected")
                C.show_df(bad)
                st.download_button("Download rejected rows", bad.to_csv(index=False).encode(), "rejected_rows.csv", "text/csv", key=f"imp_rej_{table}")
            st.markdown("**Preview of rows to import**")
            C.show_df(ok.head(200))
            if st.button(f"Import {len(ok)} row(s)", type="primary", disabled=ok.empty, key=f"imp_go_{table}"):
                try:
                    n = I.commit_import(table, ok)
                    C.refresh(); C.flash(f"Imported {n} row(s) into {LABELS[table]}."); st.rerun()
                except Exception as e:
                    st.error(f"Import failed and nothing was saved: {e}")
    with t2:
        st.markdown("**Row counts**")
        C.show_df(pd.DataFrame(db.table_counts().items(), columns=["table", "rows"]))
        st.markdown("**Reset to the simulated sample dataset**")
        st.warning("This deletes ALL current data (including anything you added or imported) and reloads the CSV files in the data folder.")
        sure = st.checkbox("I understand, replace everything", key="imp_reset_ok")
        if st.button("Reload sample data", disabled=not sure, key="imp_reset"):
            db.init_schema(); db.load_sample_data(replace=True); C.refresh(); C.flash("Sample data reloaded."); st.rerun()
