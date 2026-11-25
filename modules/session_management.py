"""Module 3: Charging session management (full CRUD)."""
import pandas as pd
import streamlit as st

import config
from modules import common as C
from services import repository as R

COLS = ["record_id", "session_id", "station_id", "charger_id", "start_ts", "end_ts", "energy_kwh",
        "session_status", "charging_cost", "is_valid", "error_messages"]


def _station_charger_pickers(data, key, default_station=None, default_charger=None):
    stations, chargers = data["stations"], data["chargers"]
    sids = list(stations.station_id)
    si = sids.index(default_station) if default_station in sids else 0
    sid = st.selectbox("Station", sids, index=si, key=f"{key}_st",
                       format_func=dict(zip(stations.station_id, stations.station_id + " - " + stations.station_name)).get)
    chs = list(chargers[chargers.station_id == sid].charger_id)
    if not chs:
        st.warning("This station has no chargers. Add one under Stations > Charging points.")
        return sid, None
    ci = chs.index(default_charger) if default_charger in chs else 0
    return sid, st.selectbox("Charger", chs, index=ci, key=f"{key}_ch_{sid}")


def session_form(data, key: str, rec: dict | None = None, submit_label: str = "Save"):
    """Returns (submitted, payload, allow_overlap, allow_dup). Station/charger pickers sit above the form so they refresh."""
    rec = rec or {}
    sid, cid = _station_charger_pickers(data, key, rec.get("station_id"), rec.get("charger_id"))
    if cid is None:
        return False, None, False, False
    with st.form(f"{key}_form_{rec.get('record_id', 'new')}_{cid}"):
        a, b = st.columns(2)
        session_id = a.text_input("Session ID", rec.get("session_id") or R.next_session_id(), max_chars=20)
        status = b.selectbox("Status", config.SESSION_STATUSES,
                             index=config.SESSION_STATUSES.index(rec.get("session_status", "Completed")))
        start = C.ts_input("Start", rec.get("start_ts"), f"{key}_start")
        end = C.ts_input("End", rec.get("end_ts"), f"{key}_end", optional=True)
        a, b = st.columns(2)
        kwh = a.number_input("Energy delivered (kWh)", min_value=0.0, value=float(max(rec.get("energy_kwh") or 10.0, 0)), step=0.5, format="%.3f")
        auto = b.checkbox("Calculate cost from the station tariff", value=True, key=f"{key}_auto")
        cost = b.number_input("Charging cost (used when not auto)", min_value=0.0, value=float(max(rec.get("charging_cost") or 0.0, 0)), step=1.0)
        with st.expander("Advanced"):
            ov = st.checkbox("Allow overlap with other sessions on this charger", key=f"{key}_ov")
            dup = st.checkbox("Allow duplicate session ID", key=f"{key}_dup")
        ok = st.form_submit_button(submit_label, type="primary")
    if not ok:
        return False, None, False, False
    if auto and start is not None:
        cost = R.session_cost(sid, kwh, start)
    return True, dict(session_id=session_id, charger_id=cid, station_id=sid, start_ts=start, end_ts=end,
                      energy_kwh=kwh, session_status=status, charging_cost=cost), ov, dup


def render(data: dict):
    C.page_header("Charging Sessions", "Browse, add, edit and delete charging sessions. Validation results are shown for every record.")
    v = data["validated"]
    tab_b, tab_a, tab_e, tab_d = st.tabs(["Browse", "Add session", "Edit session", "Delete session"])

    with tab_b:
        c1, c2, c3 = st.columns(3)
        sts = c1.multiselect("Station", sorted(v.station_id.unique()), key="ses_st")
        pool = v[v.station_id.isin(sts)] if sts else v
        chs = c2.multiselect("Charger", sorted(pool.charger_id.unique()), key="ses_ch")
        stat = c3.multiselect("Status", config.SESSION_STATUSES, key="ses_stat")
        c4, c5, c6 = st.columns([2, 1, 2])
        q = c4.text_input("Search session ID", key="ses_q").strip().lower()
        valid = c5.selectbox("Validity", ["All", "Valid only", "Invalid only"], key="ses_valid")
        use_dates = c6.checkbox("Filter by start date", key="ses_usedate")
        df = v
        if sts: df = df[df.station_id.isin(sts)]
        if chs: df = df[df.charger_id.isin(chs)]
        if stat: df = df[df.session_status.isin(stat)]
        if q: df = df[df.session_id.astype(str).str.lower().str.contains(q, regex=False)]
        if valid == "Valid only": df = df[df.is_valid]
        if valid == "Invalid only": df = df[~df.is_valid]
        if use_dates:
            lo, hi = v.start_ts.min().date(), v.start_ts.max().date()
            r = st.date_input("Start date range", (lo, hi), key="ses_dates")
            if len(r) == 2:
                df = df[(df.start_ts >= pd.Timestamp(r[0])) & (df.start_ts < pd.Timestamp(r[1]) + pd.Timedelta(days=1))]
        st.caption(f"{len(df):,} of {len(v):,} records")
        C.show_df(df[COLS])
        st.download_button("Download filtered sessions (CSV)", df[COLS].to_csv(index=False).encode(),
                           "charging_sessions_filtered.csv", "text/csv", key="ses_dl")

    with tab_a:
        ok, payload, ov, dup = session_form(data, "add", submit_label="Add session")
        if ok:
            try:
                rid = R.add_session(payload, allow_overlap=ov, allow_duplicate_id=dup)
                C.refresh(); C.flash(f"Session {payload['session_id']} added (record #{rid})."); st.rerun()
            except ValueError as e:
                st.error(str(e))

    with tab_e:
        rid = C.record_picker(v, "ses_edit", "Session to edit")
        if rid is not None:
            rec = v[v.record_id == rid].iloc[0].to_dict()
            if not rec["is_valid"]:
                st.warning("This record has validation errors: " + rec["error_messages"])
            ok, payload, ov, dup = session_form(data, "edit", rec, "Save changes")
            if ok:
                try:
                    R.update_session(int(rid), payload, allow_overlap=ov, allow_duplicate_id=dup)
                    C.refresh(); C.flash(f"Record #{rid} updated."); st.rerun()
                except ValueError as e:
                    st.error(str(e))

    with tab_d:
        rid = C.record_picker(v, "ses_del", "Session to delete")
        if rid is not None:
            row = v[v.record_id == rid].iloc[0]
            C.show_df(row[COLS].to_frame().T)
            sure = st.checkbox("I understand this permanently deletes the record and its energy readings", key=f"ses_del_ok_{rid}")
            if st.button("Delete session", type="primary", disabled=not sure, key=f"ses_del_btn_{rid}"):
                try:
                    R.delete_session(int(rid)); C.refresh(); C.flash(f"Record #{rid} deleted."); st.rerun()
                except ValueError as e:
                    st.error(str(e))
