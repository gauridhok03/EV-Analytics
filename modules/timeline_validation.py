"""Module 4: Session timeline validation, review and correction."""
import pandas as pd
import plotly.express as px
import streamlit as st

from modules import common as C
from modules.session_management import session_form
from services import repository as R
from services.validation import ERROR_LABELS, split_valid_invalid, validation_summary

SHOW = ["record_id", "session_id", "station_id", "charger_id", "start_ts", "end_ts", "start_raw", "end_raw",
        "energy_kwh", "session_status", "error_messages"]


def render(ctx: C.Ctx, data: dict):
    C.page_header("Session Timeline Validation",
                  "Every session is checked against 8 rules. Invalid records are never deleted automatically; review and correct them here.")
    v = ctx.validated
    valid, invalid = split_valid_invalid(v)
    q = ctx.data_quality()
    C.kpi_row([("Total records", C.fmt_int(q["total"])), ("Valid", C.fmt_int(q["valid"]), f"{q['valid_pct']:.1f}%"),
               ("Invalid / needs review", C.fmt_int(q["invalid"]), f"{100 - q['valid_pct']:.1f}%"),
               ("Rules checked", str(len(ERROR_LABELS)), "overlap, missing start/end, order, duplicates, energy, timestamps, length")])
    summ = validation_summary(v)
    c1, c2 = st.columns([2, 3])
    with c1:
        C.show_df(summ.rename(columns={"code": "Code", "rule": "Rule", "records": "Records"}))
    with c2:
        fig = px.bar(summ[summ.records > 0].sort_values("records"), x="records", y="rule", orientation="h", title="Records affected by rule")
        fig.update_traces(marker_color=C.PALETTE[3])
        C.show_fig(C.style_fig(fig, 300, legend=False), key="val_bar")

    t_inv, t_val, t_fix, t_rules = st.tabs(["Invalid records", "Valid records", "Review & correct", "Rules"])

    with t_inv:
        codes = st.multiselect("Filter by rule", list(ERROR_LABELS), format_func=ERROR_LABELS.get, key="val_codes")
        df = invalid
        if codes:
            df = df[df.error_codes.map(lambda c: any(x in c for x in codes))]
        st.caption(f"{len(df)} invalid record(s). Records that merely overlap an invalid neighbour are listed too.")
        cols = SHOW + (["injected_issue"] if "injected_issue" in df else [])
        C.show_df(df[cols])
        st.download_button("Download validation report (CSV)", df[cols + ["error_codes"]].astype({"error_codes": str}).to_csv(index=False).encode(),
                           "validation_report.csv", "text/csv", key="val_dl")
    with t_val:
        st.caption(f"{len(valid):,} valid record(s)")
        C.show_df(valid[SHOW[:-1]])

    with t_fix:
        if invalid.empty:
            st.success("No invalid records. Nothing to review.")
        else:
            rid = C.record_picker(invalid, "val_fix", "Invalid record")
            if rid is not None:
                rec = v[v.record_id == rid].iloc[0].to_dict()
                st.markdown("**Why this record is invalid**")
                for code in rec["error_codes"]:
                    st.error(f"{ERROR_LABELS[code]}")
                st.caption(rec["error_messages"])
                if rec.get("start_raw") or rec.get("end_raw"):
                    st.info(f"Original unparseable input kept: start = {rec.get('start_raw')!r}, end = {rec.get('end_raw')!r}")
                if pd.notna(rec["start_ts"]) or pd.notna(rec["end_ts"]):
                    anchor = rec["start_ts"] if pd.notna(rec["start_ts"]) else rec["end_ts"]
                    near = v[(v.charger_id == rec["charger_id"]) & (v.start_ts.between(anchor - pd.Timedelta(hours=36), anchor + pd.Timedelta(hours=36)))]
                    st.markdown("**Other sessions on this charger around the same time**")
                    C.show_df(near[["record_id", "session_id", "start_ts", "end_ts", "energy_kwh", "is_valid"]])

                st.markdown("**Quick fixes**")
                base = dict(session_id=rec["session_id"], charger_id=rec["charger_id"], station_id=rec["station_id"],
                            start_ts=rec["start_ts"] if pd.notna(rec["start_ts"]) else None,
                            end_ts=rec["end_ts"] if pd.notna(rec["end_ts"]) else None, energy_kwh=rec["energy_kwh"],
                            session_status=rec["session_status"], charging_cost=rec["charging_cost"])
                b1, b2, b3, b4 = st.columns(4)

                def apply(changes, ok_msg, **kw):
                    try:
                        R.update_session(int(rid), {**base, **changes}, **kw)
                        C.refresh(); C.flash(ok_msg); st.rerun()
                    except ValueError as e:
                        st.error(f"Quick fix not possible: {e}")

                if "END_BEFORE_START" in rec["error_codes"] and base["start_ts"] is not None and base["end_ts"] is not None:
                    if b1.button("Swap start and end", key=f"fx_swap_{rid}"):
                        apply({"start_ts": base["end_ts"], "end_ts": base["start_ts"]}, "Start and end swapped.")
                if "NEGATIVE_ENERGY" in rec["error_codes"]:
                    if b2.button("Use absolute energy value", key=f"fx_abs_{rid}"):
                        apply({"energy_kwh": abs(base["energy_kwh"]),
                               "charging_cost": R.session_cost(rec["station_id"], abs(base["energy_kwh"]), base["start_ts"]) if base["start_ts"] is not None else 0},
                              "Energy value corrected.")
                if "DUPLICATE_ID" in rec["error_codes"]:
                    if b3.button("Assign a new unique ID", key=f"fx_id_{rid}"):
                        apply({"session_id": R.next_session_id()}, "New session ID assigned.", allow_duplicate_id=False)
                with b4:
                    sure = st.checkbox("Confirm delete", key=f"fx_delok_{rid}")
                    if st.button("Delete record", disabled=not sure, key=f"fx_del_{rid}"):
                        R.delete_session(int(rid)); C.refresh(); C.flash(f"Record #{rid} deleted."); st.rerun()

                st.markdown("**Manual correction**")
                ok, payload, ov, dup = session_form(data, "val", {**rec, "session_status": rec["session_status"]}, "Save corrected record")
                if ok:
                    try:
                        R.update_session(int(rid), payload, allow_overlap=ov, allow_duplicate_id=dup)
                        C.refresh(); C.flash(f"Record #{rid} corrected and re-validated."); st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    with t_rules:
        st.markdown("""
| Code | Rule |
|---|---|
| MISSING_START | start time is empty and no raw text was supplied |
| MISSING_END | end time is empty (a session still *In Progress* that started within the last 24 h is allowed) |
| END_BEFORE_START | end is earlier than, or equal to, start |
| DUPLICATE_ID | the same session ID appears on more than one record (all copies are flagged) |
| NEGATIVE_ENERGY | energy delivered is below 0 |
| INVALID_TIMESTAMP | text that is not a date/time, or a date before 2000 / more than 1 day in the future |
| LONG_SESSION | duration above the configured limit (24 h) |
| OVERLAP | the session overlaps another session on the same charger (both are flagged) |
""")
        st.caption("Analytics include records whose only issue is OVERLAP (toggle in the sidebar); occupied time is merged so it is never double-counted. "
                   "Records with any other issue are excluded from analytics until corrected.")
