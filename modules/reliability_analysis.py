"""Module 7: Downtime and reliability."""
import pandas as pd
import plotly.express as px
import streamlit as st

from modules import common as C
from services import metrics as M
from services.intervals import clip_interval


def render(ctx: C.Ctx, data: dict):
    C.page_header("Downtime & Reliability", f"Observation period: {ctx.period.label}")
    C.definition("<b>Downtime</b> = union of a charger's outage intervals inside the period (overlaps merged, ongoing outages run to the end of the period). "
                 "<b>Availability %</b> = (period hours - downtime) / period hours. <b>Station availability</b> = 1 - charger downtime / (chargers x period hours). "
                 "<b>MTTR</b> = downtime / outages. <b>MTBF</b> = uptime / outages (undefined with zero outages).")
    st.write("")
    cr, sr = ctx.charger_rel, ctx.station_rel.copy()
    names = ctx.stations.set_index("station_id")["station_name"]
    sr["station"] = sr.station_id.map(names)
    ext = M.reliability_extremes(sr)
    n_out, down, up = int(cr.outages.sum()), cr.downtime_hours.sum(), cr.uptime_hours.sum()
    C.kpi_row([("Total downtime", C.fmt_hours(down), "charger-hours"), ("Outages", C.fmt_int(n_out), "merged events"),
               ("Avg outage duration", C.fmt_hours(down / n_out if n_out else None)), ("Fleet availability", C.fmt_pct(ctx.fleet_avail_pct, 2)),
               ("MTBF", C.fmt_hours(up / n_out if n_out else None), "fleet"), ("MTTR", C.fmt_hours(down / n_out if n_out else None), "fleet"),
               ("Most reliable station", names.get(ext["most"], "-")), ("Least reliable station", names.get(ext["least"], "-"))])

    a = ctx.alerts()
    if len(a):
        st.warning(f"{len(a)} alert(s)")
        C.show_df(a.drop(columns="station_id"))
    t1, t2, t3 = st.tabs(["Stations", "Chargers", "Outage log"])
    with t1:
        fig = px.bar(sr.sort_values("availability_pct"), x="availability_pct", y="station", orientation="h", title="Availability by station (%)")
        fig.update_traces(marker_color=C.PALETTE[0])
        lo = max(0, sr.availability_pct.min() - 2)
        fig.update_xaxes(range=[lo, 100])
        C.show_fig(C.style_fig(fig, 380, legend=False), key="rel_av")
        C.show_df(sr[["station_id", "station", "chargers", "outages", "downtime_hours", "availability_pct", "mtbf_hours", "mttr_hours"]]
                  .sort_values("availability_pct", ascending=False).round(2))
    with t2:
        c = cr.merge(ctx.chargers[["charger_id", "charger_type"]], on="charger_id")
        C.show_df(c[["charger_id", "station_id", "charger_type", "outages", "downtime_hours", "availability_pct", "mtbf_hours", "mttr_hours"]]
                  .sort_values("availability_pct").round(2))
    with t3:
        o = ctx.outages.copy()
        if ctx.exclude_planned:
            o = o[~o.is_planned]
        rows = []
        for r in o.itertuples():
            end = r.end_ts if pd.notna(r.end_ts) else ctx.period.end
            c = clip_interval(r.start_ts, end, ctx.period.start, ctx.period.end)
            if c:
                rows.append({"outage_id": r.outage_id, "station_id": r.station_id, "charger_id": r.charger_id, "start": c[0], "end": c[1],
                             "outage_type": r.outage_type, "planned": r.is_planned, "hours": (c[1] - c[0]).total_seconds() / 3600})
        d = pd.DataFrame(rows)
        if d.empty:
            st.success("No outages in this period.")
        else:
            by = d.groupby("outage_type", as_index=False)["hours"].sum()
            fig = px.pie(by, names="outage_type", values="hours", hole=.5, title="Outage hours by cause (raw events, overlaps not merged)")
            C.show_fig(C.style_fig(fig, 320), key="rel_pie")
            fig = px.timeline(d, x_start="start", x_end="end", y="charger_id", color="outage_type", title="Outage timeline")
            fig.update_yaxes(autorange="reversed")
            C.show_fig(C.style_fig(fig, max(320, 14 * d.charger_id.nunique() + 120)), key="rel_tl")
            C.show_df(d)
            st.download_button("Download outage log (CSV)", d.to_csv(index=False).encode(), "outages.csv", "text/csv", key="rel_dl")
