"""Module 2: Station management (stations and charging points)."""
import pandas as pd
import plotly.express as px
import streamlit as st

import config
from modules import common as C
from services import repository as R


def _sid_label(stations):
    return dict(zip(stations.station_id, stations.station_id + " - " + stations.station_name))


def render(data: dict):
    C.page_header("Station Management", "Add, update, inspect and safely remove stations and charging points.")
    stations, chargers, tariffs = data["stations"], data["chargers"], data["tariffs"]
    names = _sid_label(stations)
    tariff_names = dict(zip(tariffs.tariff_id, tariffs.tariff_name + " (\u20b9" + tariffs.price_per_kwh.map(lambda x: f"{x:g}") + "/kWh)"))
    tab_over, tab_add, tab_edit, tab_ch, tab_del = st.tabs(["Overview", "Add station", "Edit station", "Charging points", "Delete station"])

    with tab_over:
        c1, c2, c3 = st.columns([2, 1, 1])
        q = c1.text_input("Search stations (name, city, ID)", key="st_q").strip().lower()
        status = c2.multiselect("Status", config.STATION_STATUSES, key="st_status")
        city = c3.multiselect("City", sorted(stations.city.unique()), key="st_city")
        df = stations.copy()
        cnt = chargers.groupby("station_id").size().rename("chargers")
        df = df.merge(cnt, on="station_id", how="left").fillna({"chargers": 0})
        if q:
            df = df[df.station_name.str.lower().str.contains(q, regex=False) | df.city.str.lower().str.contains(q, regex=False)
                    | df.station_id.str.lower().str.contains(q, regex=False)]
        if status:
            df = df[df.status.isin(status)]
        if city:
            df = df[df.city.isin(city)]
        st.caption(f"{len(df)} of {len(stations)} stations")
        C.show_df(df[["station_id", "station_name", "city", "address", "status", "chargers", "tariff_name", "price_per_kwh", "commissioned_on"]])

        if len(df):
            st.markdown("### Station details")
            sid = st.selectbox("Station", list(df.station_id), format_func=names.get, key="st_detail")
            s = stations[stations.station_id == sid].iloc[0]
            ch = chargers[chargers.station_id == sid]
            C.kpi_row([("Status", s.status, f"since {s.commissioned_on}"), ("City", s.city, s.address or ""),
                       ("Tariff", s.tariff_name, f"\u20b9{s.price_per_kwh:g} / kWh"), ("Charging points", str(len(ch)),
                        ", ".join(f"{k}: {v}" for k, v in ch.charger_type.value_counts().items()))])
            a, b = st.columns([3, 2])
            with a:
                C.show_df(ch[["charger_id", "charger_type", "connector_type", "power_kw", "status"]])
            with b:
                try:
                    fig = px.scatter_map(pd.DataFrame([s]), lat="latitude", lon="longitude", zoom=11, height=260, hover_name="station_name", map_style="open-street-map")
                except AttributeError:
                    fig = px.scatter_mapbox(pd.DataFrame([s]), lat="latitude", lon="longitude", zoom=11, height=260, hover_name="station_name", mapbox_style="open-street-map")
                fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
                C.show_fig(fig, key="st_map")

    with tab_add:
        with st.form("add_station", clear_on_submit=False):
            a, b = st.columns(2)
            sid = a.text_input("Station ID", placeholder="ST13")
            name = b.text_input("Station name")
            city = a.text_input("City")
            addr = b.text_input("Address")
            lat = a.number_input("Latitude", -90.0, 90.0, 18.52, format="%.6f")
            lon = b.number_input("Longitude", -180.0, 180.0, 73.85, format="%.6f")
            status = a.selectbox("Status", config.STATION_STATUSES)
            tariff = b.selectbox("Tariff", list(tariff_names), format_func=tariff_names.get)
            comm = a.date_input("Commissioned on", value=pd.Timestamp.today().date())
            ok = st.form_submit_button("Add station", type="primary")
        if ok:
            try:
                new = R.add_station(dict(station_id=sid, station_name=name, city=city, address=addr, latitude=lat,
                                         longitude=lon, status=status, tariff_id=tariff, commissioned_on=comm))
                C.refresh(); C.flash(f"Station {new} added. Add its charging points in the next tab."); st.rerun()
            except ValueError as e:
                st.error(str(e))

    with tab_edit:
        sid = st.selectbox("Station to edit", list(stations.station_id), format_func=names.get, key="st_edit_sel")
        s = stations[stations.station_id == sid].iloc[0]
        with st.form(f"edit_station_{sid}"):
            a, b = st.columns(2)
            name = a.text_input("Station name", s.station_name)
            city = b.text_input("City", s.city)
            addr = a.text_input("Address", s.address or "")
            status = b.selectbox("Status", config.STATION_STATUSES, index=config.STATION_STATUSES.index(s.status))
            lat = a.number_input("Latitude", -90.0, 90.0, float(s.latitude), format="%.6f")
            lon = b.number_input("Longitude", -180.0, 180.0, float(s.longitude), format="%.6f")
            tids = list(tariff_names)
            tariff = a.selectbox("Tariff", tids, index=tids.index(int(s.tariff_id)), format_func=tariff_names.get)
            comm = b.date_input("Commissioned on", value=s.commissioned_on or pd.Timestamp.today().date())
            ok = st.form_submit_button("Save changes", type="primary")
        if ok:
            try:
                R.update_station(sid, dict(station_name=name, city=city, address=addr, latitude=lat, longitude=lon,
                                           status=status, tariff_id=tariff, commissioned_on=comm))
                C.refresh(); C.flash(f"Station {sid} updated."); st.rerun()
            except ValueError as e:
                st.error(str(e))

    with tab_ch:
        sid = st.selectbox("Station", list(stations.station_id), format_func=names.get, key="ch_station")
        ch = chargers[chargers.station_id == sid]
        C.show_df(ch[["charger_id", "charger_type", "connector_type", "power_kw", "status"]])
        a, b = st.columns(2)
        with a:
            st.markdown("**Add charging point**")
            with st.form(f"add_charger_{sid}"):
                cid = st.text_input("Charger ID", f"{sid}-C{len(ch) + 1}")
                ctype = st.selectbox("Charger type", config.CHARGER_TYPES)
                conn = st.selectbox("Connector", config.CONNECTOR_TYPES)
                power = st.number_input("Power (kW)", 1.0, 1000.0, 50.0)
                cstatus = st.selectbox("Status", config.CHARGER_STATUSES)
                ok = st.form_submit_button("Add charger", type="primary")
            if ok:
                try:
                    R.add_charger(dict(charger_id=cid, station_id=sid, charger_type=ctype, connector_type=conn,
                                       power_kw=power, status=cstatus))
                    C.refresh(); C.flash(f"Charger {cid.upper()} added."); st.rerun()
                except ValueError as e:
                    st.error(str(e))
        with b:
            st.markdown("**Edit or remove a charging point**")
            if ch.empty:
                st.info("This station has no chargers yet.")
            else:
                cid = st.selectbox("Charger", list(ch.charger_id), key=f"ch_edit_{sid}")
                c = ch[ch.charger_id == cid].iloc[0]
                with st.form(f"edit_charger_{cid}"):
                    ctype = st.selectbox("Charger type", config.CHARGER_TYPES, index=config.CHARGER_TYPES.index(c.charger_type))
                    conn = st.selectbox("Connector", config.CONNECTOR_TYPES, index=config.CONNECTOR_TYPES.index(c.connector_type))
                    power = st.number_input("Power (kW)", 1.0, 1000.0, float(c.power_kw))
                    cstatus = st.selectbox("Status", config.CHARGER_STATUSES, index=config.CHARGER_STATUSES.index(c.status))
                    ok = st.form_submit_button("Save charger")
                if ok:
                    try:
                        R.update_charger(cid, dict(charger_type=ctype, connector_type=conn, power_kw=power, status=cstatus))
                        C.refresh(); C.flash(f"Charger {cid} updated."); st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                imp = R.charger_impact(cid)
                st.caption(f"Deleting {cid} also removes {imp['sessions']} session(s) and {imp['outages']} outage(s).")
                sure = st.checkbox(f"I understand; delete {cid} and its history", key=f"ch_del_ok_{cid}")
                if st.button("Delete charger", disabled=not sure, key=f"ch_del_{cid}"):
                    try:
                        R.delete_charger(cid)
                        C.refresh(); C.flash(f"Charger {cid} deleted."); st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    with tab_del:
        sid = st.selectbox("Station to remove", list(stations.station_id), format_func=names.get, key="st_del_sel")
        imp = R.station_impact(sid)
        st.warning(f"Deleting **{sid}** permanently removes its {imp['chargers']} charger(s), {imp['sessions']:,} session(s) "
                   f"(with their energy records) and {imp['outages']} outage record(s). This cannot be undone.")
        st.markdown("Not sure? Taking the station out of service keeps all history:")
        if st.button("Mark station as Offline instead", key=f"st_off_{sid}"):
            s = stations[stations.station_id == sid].iloc[0]
            R.update_station(sid, dict(station_name=s.station_name, city=s.city, address=s.address, latitude=s.latitude,
                                       longitude=s.longitude, status="Offline", tariff_id=int(s.tariff_id),
                                       commissioned_on=s.commissioned_on))
            C.refresh(); C.flash(f"Station {sid} marked Offline."); st.rerun()
        typed = st.text_input(f"Type {sid} to confirm permanent deletion", key=f"st_del_txt_{sid}")
        if st.button("Delete station permanently", type="primary", disabled=typed.strip().upper() != sid, key=f"st_del_btn_{sid}"):
            try:
                done = R.delete_station(sid)
                C.refresh(); C.flash(f"Station {sid} deleted ({done['chargers']} chargers, {done['sessions']} sessions, {done['outages']} outages)."); st.rerun()
            except ValueError as e:
                st.error(str(e))
