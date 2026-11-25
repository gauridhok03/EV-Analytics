"""PDF performance report built with reportlab (no browser or image export needed)."""
from __future__ import annotations

import io

import pandas as pd
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY, GREEN, LIGHT = colors.HexColor("#0B1B2E"), colors.HexColor("#12A66B"), colors.HexColor("#EEF2F6")


def _table(rows, col_widths=None, header=True):
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D3DD")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT])]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def _bar(labels, values, title, width=170 * mm, height=70 * mm):
    d = Drawing(width, height)
    c = HorizontalBarChart()
    c.x, c.y, c.width, c.height = 70, 12, width - 90, height - 28
    c.data = [list(values)]
    c.categoryAxis.categoryNames = list(labels)
    c.categoryAxis.labels.fontSize = 7
    c.valueAxis.labels.fontSize = 7
    c.valueAxis.valueMin = 0
    c.bars[0].fillColor = GREEN
    c.categoryAxis.reverseDirection = 1
    d.add(c)
    return [Paragraph(f"<b>{title}</b>", getSampleStyleSheet()["Normal"]), d]


def build_pdf_report(period_label: str, kpis: dict, comparison: pd.DataFrame, alerts: pd.DataFrame,
                     validation_summary: pd.DataFrame, extremes: dict, data_quality: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm, title="EV Charging Performance Report")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=NAVY, fontSize=20, alignment=0)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=NAVY, fontSize=12, spaceBefore=10)
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#5B6B7D"))
    story = [Paragraph("EV Charging Performance Report", h1),
             Paragraph(f"Reporting period: {period_label}", ss["Normal"]),
             Paragraph("SIMULATED DATA - generated for demonstration; not real operations data.", small),
             Spacer(1, 6 * mm), Paragraph("Key indicators", h2)]
    story.append(_table([["Metric", "Value"]] + [[k, v] for k, v in kpis.items()], [90 * mm, 60 * mm]))

    story.append(Paragraph("Station performance", h2))
    cols = ["overall_rank", "station_name", "utilization_pct", "availability_pct", "sessions", "energy_kwh", "revenue", "downtime_hours"]
    head = ["#", "Station", "Util %", "Avail %", "Sessions", "kWh", "Revenue", "Down h"]
    rows = [head]
    for r in comparison[cols].itertuples(index=False):
        rows.append([r[0], str(r[1])[:26], f"{r[2]:.1f}", f"{r[3]:.2f}", int(r[4]), f"{r[5]:,.0f}", f"{r[6]:,.0f}", f"{r[7]:.1f}"])
    story.append(_table(rows, [8 * mm, 50 * mm, 17 * mm, 17 * mm, 18 * mm, 20 * mm, 24 * mm, 16 * mm]))

    if not comparison.empty:
        top = comparison.sort_values("utilization_pct", ascending=False)
        story += _bar(top["station_id"], top["utilization_pct"].fillna(0), "Utilization % by station")
    story.append(Paragraph("Reliability highlights", h2))
    names = comparison.set_index("station_id")["station_name"].to_dict()
    story.append(Paragraph(f"Most reliable station: {names.get(extremes.get('most'), '-')}<br/>"
                           f"Least reliable station: {names.get(extremes.get('least'), '-')}", ss["Normal"]))
    story.append(Paragraph("Alerts", h2))
    if alerts.empty:
        story.append(Paragraph("No alerts for this period.", ss["Normal"]))
    else:
        story.append(_table([["Station", "Alert", "Detail"]] + alerts[["station", "alert", "detail"]].values.tolist(),
                            [50 * mm, 35 * mm, 85 * mm]))
    story.append(Paragraph("Data quality", h2))
    story.append(Paragraph(f"{data_quality['valid']:,} valid and {data_quality['invalid']:,} invalid session records "
                           f"({data_quality['valid_pct']:.1f}% valid). Invalid records are kept and listed in the validation report.",
                           ss["Normal"]))
    v = validation_summary[validation_summary["records"] > 0]
    if not v.empty:
        story.append(Spacer(1, 3 * mm))
        story.append(_table([["Rule", "Records"]] + v[["rule", "records"]].values.tolist(), [110 * mm, 30 * mm]))
    doc.build(story)
    return buf.getvalue()
