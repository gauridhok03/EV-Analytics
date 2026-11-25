"""Module 9: Reports and export."""
import pandas as pd
import streamlit as st

from modules import common as C
from services import metrics as M
from services.pdf_report import build_pdf_report
from services.validation import split_valid_invalid, validation_summary

SES_COLS = ["record_id", "session_id", "station_id", "charger_id", "start_ts", "end_ts", "energy_kwh", "session_status", "charging_cost"]


def render(ctx: C.Ctx, data: dict):
    C.page_header("Reports & Export", f"Period: {ctx.period.label}")
    sp = ctx.sessions_p
    t1, t2, t3 = st.tabs(["Summaries", "CSV exports", "PDF performance report"])

    with t1:
        st.markdown("### Date-wise summary")
        d = M.daily_summary(sp)
        C.show_df(d.round(2))
        st.markdown("### Station-wise summary")
        s = ctx.comparison[["station_id", "station_name", "city", "sessions", "energy_kwh", "revenue", "avg_duration_min",
                            "utilization_pct", "availability_pct", "downtime_hours", "overall_rank"]].round(2)
        C.show_df(s)

    with t2:
        st.markdown("Exports respect the sidebar period and station filters unless stated.")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("Filtered sessions (eligible, in period)", sp[SES_COLS].to_csv(index=False).encode(), "sessions_filtered.csv", "text/csv", key="rp_ses")
            all_v = ctx.validated[SES_COLS + ["is_valid", "error_messages"]]
            st.download_button("All sessions incl. invalid, with validation result", all_v.to_csv(index=False).encode(), "sessions_all_with_validation.csv", "text/csv", key="rp_all")
            st.download_button("Station analytics", ctx.comparison.round(3).to_csv(index=False).encode(), "station_analytics.csv", "text/csv", key="rp_sta")
        with c2:
            _, inv = split_valid_invalid(ctx.validated)
            st.download_button(f"Validation report ({len(inv)} invalid records)", inv[SES_COLS + ["start_raw", "end_raw", "error_messages"]].to_csv(index=False).encode(),
                               "validation_report.csv", "text/csv", key="rp_val")
            st.download_button("Charger analytics (utilization + reliability)",
                               ctx.charger_util.merge(ctx.charger_rel[["charger_id", "outages", "availability_pct", "mtbf_hours", "mttr_hours"]], on="charger_id").round(3).to_csv(index=False).encode(),
                               "charger_analytics.csv", "text/csv", key="rp_ch")
            st.download_button("Daily summary", M.daily_summary(sp).to_csv(index=False).encode(), "daily_summary.csv", "text/csv", key="rp_day")

    with t3:
        st.markdown("A multi-page PDF with KPIs, station ranking, a utilization chart, reliability highlights, alerts and data quality.")
        if st.button("Generate PDF report", type="primary", key="rp_pdf_btn"):
            e = M.energy_summary(sp)
            q = ctx.data_quality()
            kpis = {"Stations / chargers": f"{len(ctx.stations)} / {len(ctx.chargers)}", "Sessions": f"{e['sessions']:,}",
                    "Energy delivered": f"{e['total_energy_kwh']:,.0f} kWh", "Revenue": f"INR {e['revenue']:,.0f}",
                    "Fleet utilization": C.fmt_pct(ctx.fleet_util_pct, 2), "Fleet availability": C.fmt_pct(ctx.fleet_avail_pct, 2),
                    "Total downtime": C.fmt_hours(ctx.charger_rel.downtime_hours.sum())}
            st.session_state["pdf_bytes"] = build_pdf_report(ctx.period.label, kpis, ctx.comparison, ctx.alerts(),
                                                             validation_summary(ctx.validated), M.reliability_extremes(ctx.station_rel), q)
        if st.session_state.get("pdf_bytes"):
            st.success("Report ready.")
            st.download_button("Download PDF", st.session_state["pdf_bytes"], "ev_performance_report.pdf", "application/pdf", key="rp_pdf_dl")
