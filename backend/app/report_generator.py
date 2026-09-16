"""
GUIVIN — Report Generator
Produces CSV and PDF evidence reports for submission and court records.
"""
import csv
import io
import hashlib
from datetime import datetime
from typing import List
from pathlib import Path

from .config import REPORTS_DIR


def generate_csv_report(detections: List[dict], filename: str = None) -> str:
    """Generate CSV output report of detections. Returns file path."""
    if not filename:
        filename = f"GUIVIN_Detection_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = REPORTS_DIR / filename

    fieldnames = [
        "Timestamp (IST)", "Camera ID", "Department", "District",
        "Vehicle Class", "Detected Plate", "Plate Confidence (%)",
        "Watchlist Match", "Watchlist Source", "Risk Score",
        "Alert ID", "Blockchain Block", "Blockchain Hash (SHA-256)"
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for d in detections:
            ts = d.get("timestamp", "")
            # Convert UTC to IST (+5:30)
            try:
                dt = datetime.fromisoformat(ts)
                ts_ist = dt.strftime("%Y-%m-%d %H:%M:%S IST")
            except Exception:
                ts_ist = ts

            writer.writerow({
                "Timestamp (IST)": ts_ist,
                "Camera ID": d.get("camera_id", ""),
                "Department": d.get("department", ""),
                "District": d.get("district", ""),
                "Vehicle Class": d.get("object_class", "vehicle"),
                "Detected Plate": d.get("plate_number", ""),
                "Plate Confidence (%)": f"{d.get('plate_confidence', 0) * 100:.1f}",
                "Watchlist Match": d.get("watchlist_match", "NO"),
                "Watchlist Source": d.get("watchlist_source", ""),
                "Risk Score": d.get("risk_score", 0),
                "Alert ID": d.get("alert_id", ""),
                "Blockchain Block": d.get("block_id", ""),
                "Blockchain Hash (SHA-256)": d.get("clip_hash", ""),
            })
    return str(filepath)


def generate_pdf_report(alerts: List[dict], camera_map: dict,
                         filename: str = None) -> str:
    """Generate a PDF evidence/incident report using ReportLab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table,
                                        TableStyle, Spacer, HRFlowable)
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        return ""

    if not filename:
        filename = f"GUIVIN_Incident_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    filepath = str(REPORTS_DIR / filename)

    doc = SimpleDocTemplate(filepath, pagesize=A4,
                            topMargin=20*mm, bottomMargin=20*mm,
                            leftMargin=20*mm, rightMargin=20*mm)
    styles = getSampleStyleSheet()

    NAVY = colors.HexColor("#0A1628")
    BLUE = colors.HexColor("#1E90FF")
    RED = colors.HexColor("#FF4444")
    AMBER = colors.HexColor("#FFD700")
    GREEN = colors.HexColor("#00C853")

    title_style = ParagraphStyle("title", parent=styles["Title"],
                                  fontSize=18, textColor=NAVY,
                                  alignment=TA_CENTER, spaceAfter=4)
    sub_style = ParagraphStyle("sub", parent=styles["Normal"],
                                fontSize=10, textColor=colors.grey,
                                alignment=TA_CENTER, spaceAfter=12)
    section_style = ParagraphStyle("section", parent=styles["Heading2"],
                                    fontSize=12, textColor=BLUE,
                                    spaceBefore=12, spaceAfter=6)

    story = []

    # Header
    story.append(Paragraph("GUIVIN — INCIDENT EVIDENCE REPORT", title_style))
    story.append(Paragraph(
        f"Gujarat Unified Intelligent Video Intelligence Network | "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}",
        sub_style
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE))
    story.append(Spacer(1, 8*mm))

    # Summary stats
    story.append(Paragraph("Executive Summary", section_style))
    total = len(alerts)
    high = sum(1 for a in alerts if a.get("severity") == "HIGH")
    med = sum(1 for a in alerts if a.get("severity") == "MEDIUM")
    watchlist = sum(1 for a in alerts if a.get("alert_type") == "WATCHLIST_MATCH")

    summary_data = [
        ["Total Alerts", str(total)],
        ["HIGH Severity", str(high)],
        ["MEDIUM Severity", str(med)],
        ["Watchlist Matches", str(watchlist)],
        ["Blockchain Anchored", str(sum(1 for a in alerts if a.get("block_id")))],
    ]
    summary_table = Table(summary_data, colWidths=[80*mm, 50*mm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F0F4FF")),
        ('TEXTCOLOR', (0, 0), (0, -1), NAVY),
        ('TEXTCOLOR', (1, 0), (1, -1), BLUE),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 8*mm))

    # Alert table
    story.append(Paragraph("Alert Log with Blockchain Evidence", section_style))
    headers = ["Alert ID", "Camera", "Time", "Severity", "Plate", "Risk", "Block #", "Hash (first 16)"]
    rows = [headers]
    for a in alerts[:50]:   # max 50 rows in PDF
        ts = a.get("timestamp", "")
        try:
            ts_fmt = datetime.fromisoformat(ts).strftime("%H:%M:%S")
        except Exception:
            ts_fmt = ts
        rows.append([
            a.get("id", "")[:12],
            a.get("camera_id", "")[-8:],
            ts_fmt,
            a.get("severity", ""),
            a.get("plate_number", "") or "—",
            str(a.get("risk_score", "")),
            a.get("block_id", "") or "—",
            (a.get("clip_hash", "") or "—")[:16],
        ])

    col_widths = [30*mm, 30*mm, 20*mm, 20*mm, 28*mm, 12*mm, 16*mm, 30*mm]
    alert_table = Table(rows, colWidths=col_widths, repeatRows=1)
    alert_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8FF")]),
        ('PADDING', (0, 0), (-1, -1), 4),
        ('ALIGN', (5, 0), (5, -1), 'CENTER'),
    ]))

    # Colour severity column
    for i, row in enumerate(rows[1:], 1):
        sev = row[3]
        col = RED if sev == "HIGH" else (AMBER if sev == "MEDIUM" else GREEN)
        alert_table.setStyle(TableStyle([
            ('TEXTCOLOR', (3, i), (3, i), col),
            ('FONTNAME', (3, i), (3, i), 'Helvetica-Bold'),
        ]))

    story.append(alert_table)
    story.append(Spacer(1, 8*mm))

    # Footer
    story.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey))
    story.append(Paragraph(
        "This report is generated by GUIVIN (Gujarat Unified Intelligent Video Intelligence Network). "
        "All evidence clips are SHA-256 hashed and anchored to a permissioned Hyperledger Fabric blockchain "
        "for court-admissible chain-of-custody verification. "
        "Contact: sentinel.hackathon@gujarat.gov.in",
        ParagraphStyle("footer", parent=styles["Normal"], fontSize=7,
                        textColor=colors.grey, spaceBefore=6)
    ))

    doc.build(story)
    return filepath


def get_detections_for_report(alerts: List[dict], camera_map: dict) -> List[dict]:
    """Flatten alerts + camera metadata into report rows."""
    rows = []
    for a in alerts:
        cam = camera_map.get(a.get("camera_id", ""), {})
        rows.append({
            "timestamp": a.get("timestamp", ""),
            "camera_id": a.get("camera_id", ""),
            "department": cam.get("department", ""),
            "district": cam.get("district", ""),
            "object_class": a.get("object_class", "vehicle"),
            "plate_number": a.get("plate_number", ""),
            "plate_confidence": a.get("confidence", 0),
            "watchlist_match": "YES" if a.get("alert_type") == "WATCHLIST_MATCH" else "NO",
            "watchlist_source": "",
            "risk_score": a.get("risk_score", 0),
            "alert_id": a.get("id", ""),
            "block_id": a.get("block_id", ""),
            "clip_hash": a.get("clip_hash", ""),
        })
    return rows
