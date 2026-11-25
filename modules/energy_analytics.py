"""Module 6: Energy analytics and cost."""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from modules import common as C
from services import metrics as M
from services import repository as R

FREQ = {"Daily": "D", "Weekly": "W", "Monthly": "M"}


def render(ctx: C.Ctx, data: dict):
    C.page_header("Energy Analytics", f"Reporting period: {ctx.period.label}")
    sp = ctx.sessions_p
    e = M.energy_summary(sp)
    names = ctx.stations.set_index("station_id")["station_name"]
    C.kpi_row([("Total energy delivered", f"{C.fmt_num(e['total_energy_kwh'], 0)} kWh"),
               ("Avg energy per session", f"{C.fmt_num(e['avg_energy_per_session_kwh'])} kWh"),
               ("Revenue generated", C.fmt_money(e["revenue"])),
               ("Avg price realised", f"\u20b9{e['revenue'] / e['total_energy_kwh']:.2f} / kWh" if e["total_energy_kwh"] else "-")])
    t1, t2, t3, t4 = st.tabs(["Trends", "By station & charger type", "Cost & revenue", "Cost calculator"])

    with t1:
        label = st.radio("Granularity", list(FREQ), horizontal=True, key="en_freq")
        b = M.energy_by_bucket(sp, FREQ[label])
        if b.empty:
            st.info("No sessions in this period.")
        else:
            fig = go.Figure(go.Bar(x=b.bucket_start, y=b.energy_kwh, name="Energy (kWh)", marker_color=C.PALETTE[0]))
            if len(b) >= 3:
                win = 7 if FREQ[label] == "D" else 3
                fig.add_trace(go.Scatter(x=b.bucket_start, y=b.energy_kwh.rolling(win, min_periods=1).mean(), name=f"{win}-period moving average",
                                         line=dict(color=C.PALETTE[2], width=3)))
            fig.update_layout(title=f"{label} energy delivered (kWh) and trend")
            C.show_fig(C.style_fig(fig), key="en_trend")
            C.show_df(C.rnd(b))
            st.download_button("Download (CSV)", b.to_csv(index=False).encode(), f"energy_{label.lower()}.csv", "text/csv", key="en_dl")
    with t2:
        s = M.energy_by_station(sp) if len(sp) else pd.DataFrame(columns=["station_id", "sessions", "energy_kwh", "revenue"])
        s["station"] = s.station_id.map(names)
        fig = px.bar(s.sort_values("energy_kwh"), x="energy_kwh", y="station", orientation="h", title="Energy delivered per station (kWh)")
        fig.update_traces(marker_color=C.PALETTE[0])
        C.show_fig(C.style_fig(fig, 380, legend=False), key="en_st")
        if len(sp):
            t = sp.merge(ctx.chargers[["charger_id", "charger_type"]], on="charger_id")
            g = t.groupby("charger_type", as_index=False).agg(energy_kwh=("energy_kwh", "sum"), sessions=("session_id", "count"))
            fig = px.pie(g, names="charger_type", values="energy_kwh", hole=.5, title="Energy share by charger type")
            C.show_fig(C.style_fig(fig, 320), key="en_type")
    with t3:
        label = st.radio("Granularity ", list(FREQ), horizontal=True, key="en_freq2")
        b = M.energy_by_bucket(sp, FREQ[label])
        if len(b):
            fig = px.line(b, x="bucket_start", y="revenue", markers=True, title=f"{label} revenue (\u20b9)")
            fig.update_traces(line_color=C.PALETTE[2])
            C.show_fig(C.style_fig(fig, legend=False), key="en_rev")
        s = M.energy_by_station(sp) if len(sp) else pd.DataFrame()
        if len(s):
            s["station"] = s.station_id.map(names)
            s["avg_cost_per_session"] = s.revenue / s.sessions
            C.show_df(s[["station_id", "station", "sessions", "energy_kwh", "revenue", "avg_cost_per_session"]].round(2))
    with t4:
        st.markdown("Estimate the cost of a charge using the station's tariff, including its peak-hour multiplier.")
        sids = list(ctx.data["stations"].station_id)
        sid = st.selectbox("Station", sids, key="cc_st", format_func=lambda s: f"{s} - {ctx.data['stations'].set_index('station_id').station_name[s]}")
        kwh = st.number_input("Energy (kWh)", 0.1, 500.0, 30.0, key="cc_kwh")
        d = st.date_input("Start date", value=pd.Timestamp.today().date(), key="cc_d")
        t = st.time_input("Start time", value=pd.Timestamp("2026-01-01 19:00").time(), key="cc_t")
        cost = R.session_cost(sid, kwh, pd.Timestamp.combine(d, t))
        tr = ctx.data["stations"].set_index("station_id").loc[sid]
        st.metric("Estimated cost", f"\u20b9{cost:,.2f}")
        st.caption(f"Tariff: {tr.tariff_name} at \u20b9{tr.price_per_kwh:g}/kWh.")
