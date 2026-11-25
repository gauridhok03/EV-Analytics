"""Module 1: Dashboard."""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from modules import common as C
from services import metrics as M


def render(ctx: C.Ctx, data: dict):
    C.page_header("Dashboard", f"Reporting period: {ctx.period.label}")
    sp = ctx.sessions_p
    en = M.energy_summary(sp)
    down = ctx.charger_rel["downtime_hours"].sum()
    C.kpi_row([
        ("Charging stations", C.fmt_int(len(ctx.stations)), f"{(ctx.stations.status == 'Operational').sum()} operational"),
        ("Chargers", C.fmt_int(len(ctx.chargers)), f"{ctx.chargers.charger_type.nunique()} charger types"),
        ("Charging sessions", C.fmt_int(en["sessions"]), "eligible sessions in period"),
        ("Energy delivered", f"{C.fmt_num(en['total_energy_kwh'], 0)} kWh", f"avg {C.fmt_num(en['avg_energy_per_session_kwh'])} kWh / session"),
        ("Avg station utilization", C.fmt_pct(ctx.station_util['utilization_pct'].mean()), f"fleet time-weighted {C.fmt_pct(ctx.fleet_util_pct)}"),
        ("Avg station availability", C.fmt_pct(ctx.station_rel['availability_pct'].mean(), 2), f"fleet {C.fmt_pct(ctx.fleet_avail_pct, 2)}"),
        ("Total downtime", C.fmt_hours(down), "charger-hours, overlaps merged"),
        ("Electricity revenue", C.fmt_money(en["revenue"]), "sum of session charging cost"),
    ])

    b = M.utilization_by_bucket(ctx.chargers, ctx.eligible, ctx.outages, ctx.period, "D", ctx.exclude_planned)
    fleet = M.fleet_utilization_by_bucket(b)
    daily = M.energy_by_bucket(sp, "D")
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure(go.Bar(x=daily["bucket_start"], y=daily["energy_kwh"], name="Energy (kWh)", marker_color=C.PALETTE[0]))
        fig.update_layout(title="Daily energy delivered (kWh)")
        C.show_fig(C.style_fig(fig, legend=False), key="dash_energy")
    with c2:
        fig = px.line(fleet, x="bucket_start", y="utilization_pct", title="Daily fleet utilization (%)")
        fig.update_traces(line_color=C.PALETTE[1])
        fig.update_yaxes(rangemode="tozero")
        C.show_fig(C.style_fig(fig, legend=False), key="dash_util")

    c3, c4 = st.columns(2)
    with c3:
        su = ctx.station_util.merge(ctx.stations[["station_id", "station_name"]], on="station_id").sort_values("utilization_pct")
        fig = px.bar(su, x="utilization_pct", y="station_name", orientation="h", title="Utilization by station (%)")
        fig.update_traces(marker_color=C.PALETTE[0])
        C.show_fig(C.style_fig(fig, 380, legend=False), key="dash_su")
    with c4:
        h = M.hourly_profile(ctx.eligible, ctx.period)
        fig = px.bar(h, x="hour", y="occupied_hours", title="Peak charging hours (occupied hours by hour of day)")
        fig.update_traces(marker_color=C.PALETTE[2])
        fig.update_xaxes(dtick=2)
        C.show_fig(C.style_fig(fig, 380, legend=False), key="dash_hours")

    st.markdown("### Station map")
    m = ctx.stations.merge(ctx.station_util[["station_id", "utilization_pct"]], on="station_id") \
        .merge(ctx.station_rel[["station_id", "availability_pct"]], on="station_id")
    m["size"] = m["utilization_pct"].fillna(0).clip(lower=1)
    kw = dict(lat="latitude", lon="longitude", size="size", color="utilization_pct", hover_name="station_name",
              hover_data={"city": True, "status": True, "utilization_pct": ":.1f", "availability_pct": ":.2f", "size": False,
                          "latitude": False, "longitude": False},
              color_continuous_scale=["#5B8DEF", "#3DDC97"], zoom=5, height=420,
              center={"lat": float(m.latitude.mean()), "lon": float(m.longitude.mean())})
    try:
        fig = px.scatter_map(m, map_style="open-street-map", **kw)
    except AttributeError:  # older plotly
        fig = px.scatter_mapbox(m, mapbox_style="open-street-map", **kw)
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)")
    C.show_fig(fig, key="dash_map")

    left, right = st.columns([3, 2])
    with left:
        st.markdown("### Alerts")
        a = ctx.alerts()
        if a.empty:
            st.success(f"No alerts: every station is above {C.config.LOW_AVAILABILITY_PCT:g}% availability and below "
                       f"{C.config.HIGH_DOWNTIME_HOURS:g} downtime hours.")
        else:
            C.show_df(a.drop(columns="station_id"))
    with right:
        st.markdown("### Data quality")
        q = ctx.data_quality()
        st.metric("Valid session records", f"{q['valid_pct']:.1f}%", f"{q['invalid']} need review", delta_color="off")
        st.caption("Invalid records stay in the database; see Timeline Validation to review and correct them.")
