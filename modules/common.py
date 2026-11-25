"""Shared UI helpers: theme, cached data loading, sidebar filters, analysis context, KPI cards."""
from __future__ import annotations

from datetime import date, datetime
from functools import cached_property

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import database as db
from services import metrics as M
from services.intervals import Period, make_period
from services.validation import analytics_eligible, validate_sessions

PALETTE = ["#3DDC97", "#5B8DEF", "#F5B94A", "#E8618C", "#9B7BEA", "#4FC3D9", "#F28C52", "#A3D977",
           "#7C93AB", "#D96BD0", "#58C1A0", "#C9A66B"]


# ----------------------------------------------------------------------------- generic helpers
def show_df(df: pd.DataFrame, **kw):
    try:
        return st.dataframe(df, width="stretch", hide_index=True, **kw)
    except TypeError:  # older Streamlit
        return st.dataframe(df, use_container_width=True, hide_index=True, **kw)


def show_fig(fig: go.Figure, key: str | None = None):
    try:
        return st.plotly_chart(fig, width="stretch", key=key)
    except TypeError:
        return st.plotly_chart(fig, use_container_width=True, key=key)


def rnd(df: pd.DataFrame, d: int = 2) -> pd.DataFrame:
    """Round numeric columns only (DataFrame.round warns on datetime columns)."""
    out = df.copy()
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(d)
    return out


def refresh() -> None:
    """Drop cached query results after any write."""
    st.cache_data.clear()


def flash(msg: str, kind: str = "success") -> None:
    st.session_state["_flash"] = (kind, msg)


def show_flash() -> None:
    f = st.session_state.pop("_flash", None)
    if f:
        {"success": st.success, "error": st.error, "warning": st.warning, "info": st.info}[f[0]](f[1])


def fmt_int(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:,.0f}"


def fmt_num(x, d=1) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:,.{d}f}"


def fmt_money(x) -> str:
    return "-" if x is None or pd.isna(x) else f"\u20b9{x:,.0f}"


def fmt_pct(x, d=1) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:.{d}f}%"


def fmt_hours(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:,.1f} h"


def theme() -> dict:
    return config.THEMES["light" if st.session_state.get("light_theme") else "dark"]


def style_fig(fig: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    t = theme()
    fig.update_layout(template=t["plotly_template"], height=height, margin=dict(l=10, r=10, t=40, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", colorway=PALETTE,
                      font=dict(color=t["text"], size=12), showlegend=legend,
                      title=dict(font=dict(size=14)), legend=dict(orientation="h", y=-0.2))
    fig.update_xaxes(gridcolor=t["border"], zerolinecolor=t["border"])
    fig.update_yaxes(gridcolor=t["border"], zerolinecolor=t["border"])
    return fig


def inject_css() -> None:
    t = theme()
    side = "#08131F" if not st.session_state.get("light_theme") else "#FFFFFF"
    st.markdown(f"""<style>
    .stApp {{ background: {t['bg']}; color: {t['text']}; }}
    [data-testid="stSidebar"] {{ background: {side}; border-right: 1px solid {t['border']}; }}
    [data-testid="stSidebar"] * {{ color: {t['text']}; }}
    h1, h2, h3, h4, h5, label, .stMarkdown p, .stCaption {{ color: {t['text']}; }}
    h1 {{ font-size: 1.7rem; font-weight: 650; letter-spacing: -0.01em; margin-bottom: .1rem; }}
    h3 {{ font-size: 1.05rem; font-weight: 600; }}
    .block-container {{ padding-top: 2rem; max-width: 1400px; }}
    .brand {{ font-size: 1.15rem; font-weight: 700; letter-spacing: .02em; }}
    .brand span {{ color: {t['accent']}; }}
    .brand-sub {{ font-size: .72rem; color: {t['muted']}; margin-bottom: .8rem; }}
    .kpi {{ background: {t['panel']}; border: 1px solid {t['border']}; border-left: 3px solid {t['accent']};
            border-radius: 8px; padding: 14px 16px; min-height: 92px; }}
    .kpi-label {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: {t['muted']}; }}
    .kpi-value {{ font-size: 1.6rem; font-weight: 650; line-height: 1.25; color: {t['text']}; }}
    .kpi-sub {{ font-size: .72rem; color: {t['muted']}; }}
    .pagehead {{ color: {t['muted']}; font-size: .85rem; margin-bottom: 1rem; }}
    .sim-badge {{ display:inline-block; font-size:.68rem; padding:2px 8px; border-radius:20px;
                  border:1px solid {t['accent']}; color:{t['accent']}; margin-left:8px; vertical-align:middle; }}
    .defn {{ background:{t['panel']}; border:1px solid {t['border']}; border-radius:8px; padding:10px 14px;
             font-size:.82rem; color:{t['muted']}; }}
    </style>""", unsafe_allow_html=True)


def apply_native_theme() -> None:
    """Best-effort switch of Streamlit's own widget theme (process-wide, local use)."""
    t = theme()
    try:
        for k, v in {"base": "light" if st.session_state.get("light_theme") else "dark",
                     "primaryColor": t["accent"], "backgroundColor": t["bg"],
                     "secondaryBackgroundColor": t["panel"], "textColor": t["text"]}.items():
            st._config.set_option(f"theme.{k}", v)
    except Exception:
        pass


def page_header(title: str, caption: str = "", simulated: bool = True) -> None:
    badge = '<span class="sim-badge">SIMULATED DATA</span>' if simulated else ""
    st.markdown(f"<h1>{title}{badge}</h1>", unsafe_allow_html=True)
    if caption:
        st.markdown(f'<div class="pagehead">{caption}</div>', unsafe_allow_html=True)
    show_flash()


def kpi_row(items: list[tuple], per_row: int = 4) -> None:
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col, it in zip(cols, items[i:i + per_row]):
            label, value, *sub = it
            col.markdown(f'<div class="kpi"><div class="kpi-label">{label}</div>'
                         f'<div class="kpi-value">{value}</div><div class="kpi-sub">{sub[0] if sub else "&nbsp;"}</div></div>',
                         unsafe_allow_html=True)
        st.write("")


def definition(text: str) -> None:
    st.markdown(f'<div class="defn">{text}</div>', unsafe_allow_html=True)


# ----------------------------------------------------------------------------- data
@st.cache_data(show_spinner="Loading data...")
def load_data() -> dict:
    sessions = db.load_sessions()
    return {"stations": db.load_stations(), "chargers": db.load_chargers(), "sessions": sessions,
            "outages": db.load_outages(), "tariffs": db.load_tariffs(),
            "validated": validate_sessions(sessions)}


class Ctx:
    """Everything an analytics page needs, already filtered by the sidebar selections."""

    def __init__(self, data: dict, station_ids: list[str], period: Period,
                 include_overlaps: bool, exclude_planned: bool):
        self.data, self.station_ids, self.period = data, station_ids, period
        self.include_overlaps, self.exclude_planned = include_overlaps, exclude_planned
        self.stations = data["stations"][data["stations"].station_id.isin(station_ids)].reset_index(drop=True)
        self.chargers = data["chargers"][data["chargers"].station_id.isin(station_ids)].reset_index(drop=True)
        self.tariffs = data["tariffs"]
        self.validated = data["validated"][data["validated"].station_id.isin(station_ids)]
        self.eligible = analytics_eligible(self.validated, include_overlaps)
        self.sessions_p = M.sessions_in_period(self.eligible, period)
        self.outages = data["outages"][data["outages"].station_id.isin(station_ids)]

    @cached_property
    def charger_util(self):
        return M.charger_utilization(self.chargers, self.eligible, self.outages, self.period, self.exclude_planned)

    @cached_property
    def station_util(self):
        return M.station_utilization(self.charger_util)

    @cached_property
    def charger_rel(self):
        return M.charger_reliability(self.chargers, self.outages, self.period, self.exclude_planned)

    @cached_property
    def station_rel(self):
        return M.station_reliability(self.charger_rel)

    @cached_property
    def comparison(self):
        return M.station_comparison(self.stations, self.station_util, self.station_rel, self.sessions_p)

    @cached_property
    def fleet_util_pct(self):
        c = self.charger_util
        a = c["available_hours"].sum()
        return c["occupied_hours"].sum() / a * 100 if a > 0 else float("nan")

    @cached_property
    def fleet_avail_pct(self):
        r = self.charger_rel
        t = r["uptime_hours"].sum() + r["downtime_hours"].sum()
        return r["uptime_hours"].sum() / t * 100 if t > 0 else float("nan")

    def alerts(self):
        return M.generate_alerts(self.station_rel, self.stations, config.LOW_AVAILABILITY_PCT, config.HIGH_DOWNTIME_HOURS)

    def data_quality(self) -> dict:
        v = self.validated
        n, ok = len(v), int(v["is_valid"].sum())
        return {"total": n, "valid": ok, "invalid": n - ok, "valid_pct": ok / n * 100 if n else 100.0}


def sidebar_filters(data: dict) -> Ctx | None:
    stations = data["stations"]
    el = data["validated"]
    el = el[el.is_valid & el.start_ts.notna()]
    if len(el):
        d0, d1 = el.start_ts.min().date(), el.start_ts.max().date()
    else:
        d1 = date.today(); d0 = d1 - pd.Timedelta(days=30)
    with st.sidebar.expander("Analysis filters", expanded=True):
        rng = st.date_input("Reporting period", value=(d0, d1), key="period")
        names = dict(zip(stations.station_id, stations.station_id + " - " + stations.station_name))
        sel = st.multiselect("Stations", list(stations.station_id), default=list(stations.station_id),
                             format_func=names.get, key="station_filter")
        inc = st.toggle("Include overlapping sessions", value=True, key="inc_overlap",
                        help="Sessions whose only problem is an overlap still count; occupied time is never double-counted.")
        excl = st.toggle("Exclude planned maintenance", value=False, key="excl_planned",
                         help="Treat scheduled maintenance as available time.")
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.info("Select both a start and an end date in the sidebar.")
        return None
    if not sel:
        st.info("Select at least one station in the sidebar.")
        return None
    return Ctx(data, sel, make_period(rng[0], rng[1]), inc, excl)


def ts_input(label: str, default, key: str, optional: bool = False):
    """Date + time inputs -> Timestamp (or None when optional and the box is ticked)."""
    base = pd.Timestamp(default) if default is not None and not pd.isna(default) else pd.Timestamp.now().floor("min")
    none = False
    if optional:
        none = st.checkbox(f"{label}: no value (still in progress)", value=default is None or pd.isna(default), key=f"{key}_none")
    c1, c2 = st.columns(2)
    d = c1.date_input(f"{label} date", value=base.date(), key=f"{key}_d")
    t = c2.time_input(f"{label} time", value=base.time().replace(second=0, microsecond=0), key=f"{key}_t", step=60)
    return None if none else pd.Timestamp(datetime.combine(d, t))


def record_picker(df: pd.DataFrame, key: str, label: str = "Record") -> int | None:
    """Search box + selectbox returning a record_id."""
    q = st.text_input("Search by session ID, charger or station", key=f"{key}_q").strip().lower()
    d = df
    if q:
        m = d["session_id"].astype(str).str.lower().str.contains(q, regex=False) | \
            d["charger_id"].str.lower().str.contains(q, regex=False) | d["station_id"].str.lower().str.contains(q, regex=False)
        d = d[m]
    d = d.head(300)
    if d.empty:
        st.info("No matching records.")
        return None
    labels = {int(r.record_id): f"#{r.record_id} | {r.session_id} | {r.charger_id} | {r.start_ts if pd.notna(r.start_ts) else 'no start'}"
              for r in d.itertuples()}
    return st.selectbox(label, list(labels), format_func=labels.get, key=f"{key}_sel")
