"""Module 5: Utilization analytics."""
import pandas as pd
import plotly.express as px
import streamlit as st

from modules import common as C
from services import metrics as M

FREQ = {"Daily": "D", "Weekly": "W", "Monthly": "M"}


def render(ctx: C.Ctx, data: dict):
    C.page_header("Utilization Analytics", f"Reporting period: {ctx.period.label}")
    C.definition("<b>Utilization (%) = Occupied time / Available time x 100.</b> Available time = period hours minus outage hours "
                 "(overlapping outages merged). Occupied time = union of session intervals (overlapping sessions counted once) "
                 "excluding time the charger was down. Station utilization is time-weighted across its chargers.")
    st.write("")
    cu, su = ctx.charger_util.copy(), ctx.station_util.copy()
    names = ctx.stations.set_index("station_id")["station_name"]
    su["station"] = su.station_id.map(names)
    C.kpi_row([("Fleet utilization", C.fmt_pct(ctx.fleet_util_pct, 2)),
               ("Avg charging duration", f"{C.fmt_num(cu['avg_duration_min'].mean(), 0)} min"),
               ("Sessions in period", C.fmt_int(cu["sessions"].sum())),
               ("Sessions per charger", C.fmt_num(cu["sessions"].mean(), 1), "average")])
    t1, t2, t3, t4 = st.tabs(["Chargers", "Stations", "Daily / weekly / monthly", "Peak hours"])

    with t1:
        c = cu.merge(ctx.stations[["station_id", "station_name"]], on="station_id")
        fig = px.bar(c.sort_values("utilization_pct", ascending=False), x="charger_id", y="utilization_pct", color="charger_type",
                     title="Utilization by charger (%)")
        C.show_fig(C.style_fig(fig, 360), key="ut_ch")
        C.show_df(c[["charger_id", "station_name", "charger_type", "utilization_pct", "occupied_hours", "available_hours",
                     "downtime_hours", "sessions", "avg_duration_min"]].round(2))
    with t2:
        fig = px.bar(su.sort_values("utilization_pct"), x="utilization_pct", y="station", orientation="h", title="Utilization by station (%)")
        fig.update_traces(marker_color=C.PALETTE[0])
        C.show_fig(C.style_fig(fig, 380, legend=False), key="ut_st")
        C.show_df(su[["station_id", "station", "chargers", "utilization_pct", "occupied_hours", "available_hours", "sessions"]].round(2))
    with t3:
        label = st.radio("Granularity", list(FREQ), horizontal=True, key="ut_freq")
        b = M.utilization_by_bucket(ctx.chargers, ctx.eligible, ctx.outages, ctx.period, FREQ[label], ctx.exclude_planned)
        fleet = M.fleet_utilization_by_bucket(b)
        fig = px.line(fleet, x="bucket_start", y="utilization_pct", markers=True, title=f"{label} fleet utilization (%)")
        fig.update_yaxes(rangemode="tozero")
        C.show_fig(C.style_fig(fig, legend=False), key="ut_fleet")
        b["station"] = b.station_id.map(names)
        fig = px.line(b, x="bucket_start", y="utilization_pct", color="station", title=f"{label} utilization by station (%)")
        C.show_fig(C.style_fig(fig, 380), key="ut_bucket_st")
        out = C.rnd(fleet.rename(columns={"bucket_start": "period_start"}))
        C.show_df(out)
        st.download_button("Download this table (CSV)", out.to_csv(index=False).encode(), f"utilization_{label.lower()}.csv", "text/csv", key="ut_dl")
    with t4:
        h = M.hourly_profile(ctx.eligible, ctx.period)
        peak = h.sort_values("occupied_hours", ascending=False).iloc[0]
        st.info(f"Peak hour: {int(peak.hour):02d}:00-{int(peak.hour) + 1:02d}:00 with {peak.occupied_hours:.1f} occupied charger-hours "
                f"and {int(peak.session_starts)} session starts in the hour that starts sessions most.")
        fig = px.bar(h, x="hour", y="occupied_hours", title="Occupied hours by hour of day"); fig.update_traces(marker_color=C.PALETTE[2])
        C.show_fig(C.style_fig(fig, legend=False), key="ut_h1")
        sp = ctx.sessions_p
        if len(sp):
            hm = sp.assign(weekday=sp.start_ts.dt.day_name(), hour=sp.start_ts.dt.hour).groupby(["weekday", "hour"]).size().reset_index(name="sessions")
            order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            pv = hm.pivot(index="weekday", columns="hour", values="sessions").reindex(order).fillna(0)
            fig = px.imshow(pv, aspect="auto", color_continuous_scale=["#12263D", "#3DDC97"], title="Session starts: weekday x hour")
            C.show_fig(C.style_fig(fig, legend=False), key="ut_heat")
