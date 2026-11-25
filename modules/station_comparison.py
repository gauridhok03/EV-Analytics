"""Module 8: Station comparison and ranking."""
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from modules import common as C

METRICS = {"utilization_pct": ("Utilization %", False), "energy_kwh": ("Energy (kWh)", False), "sessions": ("Sessions", False),
           "avg_duration_min": ("Avg duration (min)", False), "downtime_hours": ("Downtime (h)", True),
           "availability_pct": ("Availability %", False), "revenue": ("Revenue (\u20b9)", False)}


def render(ctx: C.Ctx, data: dict):
    C.page_header("Station Comparison", f"Period: {ctx.period.label}")
    comp = ctx.comparison
    if len(comp) < 2:
        st.info("Select at least two stations in the sidebar to compare.")
        if len(comp) == 0:
            return
    pick = st.multiselect("Stations to compare", list(comp.station_id), default=list(comp.station_id),
                          format_func=dict(zip(comp.station_id, comp.station_name)).get, key="cmp_pick")
    df = comp[comp.station_id.isin(pick)].copy()
    if df.empty:
        st.info("Pick at least one station.")
        return
    df["overall_rank"] = df["overall_rank_score"].rank(method="min").astype(int)
    df = df.sort_values("overall_rank")
    st.markdown("### Ranking")
    show = df[["overall_rank", "station_id", "station_name", "city"] + list(METRICS)].round(2)
    C.show_df(show.rename(columns={k: v[0] for k, v in METRICS.items()}))
    st.caption("Overall rank = average of the per-metric ranks (utilization, energy, sessions, revenue, availability, downtime; lower downtime is better).")
    st.download_button("Download comparison (CSV)", show.to_csv(index=False).encode(), "station_comparison.csv", "text/csv", key="cmp_dl")

    metric = st.selectbox("Metric to chart", list(METRICS), format_func=lambda k: METRICS[k][0], key="cmp_metric")
    fig = px.bar(df.sort_values(metric, ascending=not METRICS[metric][1]), x=metric, y="station_name", orientation="h", title=METRICS[metric][0])
    fig.update_traces(marker_color=C.PALETTE[0])
    C.show_fig(C.style_fig(fig, max(300, 34 * len(df) + 100), legend=False), key="cmp_bar")

    c1, c2 = st.columns(2)
    with c1:
        fig = px.scatter(df, x="utilization_pct", y="availability_pct", size=df.energy_kwh.clip(lower=1), color="city", hover_name="station_name",
                         title="Utilization vs availability (bubble = energy)")
        C.show_fig(C.style_fig(fig, 380), key="cmp_sc")
    with c2:
        if len(df) <= 6:
            fig = go.Figure()
            for r in df.itertuples():
                vals = []
                for m, (_, lower_better) in METRICS.items():
                    col = df[m].astype(float)
                    rng = col.max() - col.min()
                    x = (getattr(r, m) - col.min()) / rng if rng else 1.0
                    vals.append(1 - x if lower_better else x)
                fig.add_trace(go.Scatterpolar(r=vals + vals[:1], theta=[v[0] for v in METRICS.values()] + [METRICS["utilization_pct"][0]],
                                              fill="toself", name=r.station_name, opacity=.55))
            fig.update_layout(title="Normalised profile (outer = better)")
            C.show_fig(C.style_fig(fig, 380), key="cmp_radar")
        else:
            st.info("Select 6 or fewer stations to see the radar comparison.")
