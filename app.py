import csv
import io
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, UploadFile, File, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from database import engine, get_db, Base
from models import Reading, Alert, AlertEvent

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Hanna Hub", description="HI6000 Water Quality Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

API_KEY = os.environ.get("API_KEY", "hanna-hub-key")

PARAM_LABELS = {
    "ph": "pH",
    "orp_mv": "ORP (mV)",
    "do_mgl": "DO (mg/L)",
    "do_pct": "DO (%)",
    "ec_us": "EC (µS/cm)",
    "tds_mgl": "TDS (mg/L)",
    "temp_c": "Temp (°C)",
    "ise_value": "ISE",
}

CSV_FIELD_MAP = {
    "ph": "ph",
    "mv": "orp_mv",
    "orp": "orp_mv",
    "orp (mv)": "orp_mv",
    "do (mg/l)": "do_mgl",
    "do mg/l": "do_mgl",
    "dissolved oxygen (mg/l)": "do_mgl",
    "do (%)": "do_pct",
    "do %": "do_pct",
    "dissolved oxygen (%)": "do_pct",
    "ec (µs/cm)": "ec_us",
    "ec (us/cm)": "ec_us",
    "conductivity (µs/cm)": "ec_us",
    "tds (mg/l)": "tds_mgl",
    "tds mg/l": "tds_mgl",
    "temperature (°c)": "temp_c",
    "temperature (c)": "temp_c",
    "temp (°c)": "temp_c",
    "temp": "temp_c",
    "temp (c)": "temp_c",
    "ise": "ise_value",
    "ion": "ise_value",
    "sample id": "sample_id",
    "sample": "sample_id",
    "operator": "operator",
    "date": "_date",
    "time": "_time",
}


def check_alerts(db: Session, reading: Reading):
    alerts = db.query(Alert).filter(Alert.active == 1).all()
    for alert in alerts:
        val = getattr(reading, alert.parameter, None)
        if val is None:
            continue
        triggered = False
        if alert.condition == "gt" and val > alert.threshold:
            triggered = True
        elif alert.condition == "lt" and val < alert.threshold:
            triggered = True
        elif alert.condition == "eq" and val == alert.threshold:
            triggered = True
        if triggered:
            event = AlertEvent(
                alert_id=alert.id,
                reading_id=reading.id,
                value=val,
                message=f"{PARAM_LABELS.get(alert.parameter, alert.parameter)} is {val} ({alert.condition} {alert.threshold}): {alert.label}",
            )
            db.add(event)
    db.commit()


# ── Dashboard ──

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    latest = db.query(Reading).order_by(desc(Reading.timestamp)).first()
    recent_alerts = (
        db.query(AlertEvent).order_by(desc(AlertEvent.timestamp)).limit(10).all()
    )
    count = db.query(func.count(Reading.id)).scalar()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "latest": latest,
            "alert_events": recent_alerts,
            "total_readings": count,
            "params": PARAM_LABELS,
        },
    )


# ── API: Ingest single reading (from bridge script) ──

@app.post("/api/readings")
def create_reading(
    data: dict,
    key: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if key != API_KEY:
        return JSONResponse({"error": "invalid api key"}, status_code=401)
    reading = Reading(
        ph=data.get("ph"),
        orp_mv=data.get("orp_mv"),
        do_mgl=data.get("do_mgl"),
        do_pct=data.get("do_pct"),
        ec_us=data.get("ec_us"),
        tds_mgl=data.get("tds_mgl"),
        temp_c=data.get("temp_c"),
        ise_value=data.get("ise_value"),
        ise_unit=data.get("ise_unit"),
        sample_id=data.get("sample_id"),
        operator=data.get("operator"),
        source=data.get("source", "bridge"),
    )
    if data.get("timestamp"):
        reading.timestamp = datetime.fromisoformat(data["timestamp"])
    db.add(reading)
    db.commit()
    db.refresh(reading)
    check_alerts(db, reading)
    return {"id": reading.id, "timestamp": str(reading.timestamp)}


# ── API: CSV upload ──

@app.post("/api/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    key: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if key != API_KEY:
        return JSONResponse({"error": "invalid api key"}, status_code=401)

    content = await file.read()
    text = content.decode("utf-8-sig")
    lines = text.strip().splitlines()

    # Skip Hanna metadata header lines (lines starting with meter info)
    data_start = 0
    for i, line in enumerate(lines):
        if "," in line:
            parts = line.split(",")
            lower = parts[0].strip().lower()
            if lower in CSV_FIELD_MAP or lower == "date" or lower == "time":
                data_start = i
                break
    if data_start > 0:
        lines = lines[data_start:]

    reader = csv.DictReader(lines)
    imported = 0
    for row in reader:
        reading = Reading(source="csv")
        date_str = ""
        time_str = ""
        for csv_col, value in row.items():
            if not csv_col or not value:
                continue
            col_lower = csv_col.strip().lower()
            db_field = CSV_FIELD_MAP.get(col_lower)
            if not db_field:
                continue
            value = value.strip()
            if db_field == "_date":
                date_str = value
            elif db_field == "_time":
                time_str = value
            elif db_field in ("sample_id", "operator", "ise_unit"):
                setattr(reading, db_field, value)
            else:
                try:
                    setattr(reading, db_field, float(value))
                except ValueError:
                    pass

        if date_str:
            ts_str = f"{date_str} {time_str}".strip()
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%m/%d/%Y %H:%M",
                "%Y-%m-%d",
                "%m/%d/%Y",
            ):
                try:
                    reading.timestamp = datetime.strptime(ts_str, fmt).replace(
                        tzinfo=timezone.utc
                    )
                    break
                except ValueError:
                    continue

        db.add(reading)
        imported += 1

    db.commit()
    return {"imported": imported, "filename": file.filename}


# ── API: Query readings ──

@app.get("/api/readings")
def get_readings(
    hours: int = Query(default=24),
    param: Optional[str] = Query(default=None),
    limit: int = Query(default=500),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = db.query(Reading).filter(Reading.timestamp >= cutoff)
    if param and param in PARAM_LABELS:
        q = q.filter(getattr(Reading, param).isnot(None))
    rows = q.order_by(Reading.timestamp).limit(limit).all()
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "ph": r.ph,
            "orp_mv": r.orp_mv,
            "do_mgl": r.do_mgl,
            "do_pct": r.do_pct,
            "ec_us": r.ec_us,
            "tds_mgl": r.tds_mgl,
            "temp_c": r.temp_c,
            "ise_value": r.ise_value,
            "ise_unit": r.ise_unit,
            "sample_id": r.sample_id,
            "operator": r.operator,
            "source": r.source,
        }
        for r in rows
    ]


# ── API: Export CSV ──

@app.get("/api/export-csv")
def export_csv(
    hours: int = Query(default=168),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(Reading)
        .filter(Reading.timestamp >= cutoff)
        .order_by(Reading.timestamp)
        .all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Timestamp",
            "pH",
            "ORP (mV)",
            "DO (mg/L)",
            "DO (%)",
            "EC (µS/cm)",
            "TDS (mg/L)",
            "Temp (°C)",
            "ISE Value",
            "ISE Unit",
            "Sample ID",
            "Operator",
            "Source",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.timestamp.isoformat() if r.timestamp else "",
                r.ph or "",
                r.orp_mv or "",
                r.do_mgl or "",
                r.do_pct or "",
                r.ec_us or "",
                r.tds_mgl or "",
                r.temp_c or "",
                r.ise_value or "",
                r.ise_unit or "",
                r.sample_id or "",
                r.operator or "",
                r.source or "",
            ]
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=hanna-hub-export.csv"},
    )


# ── Alerts CRUD ──

@app.get("/api/alerts")
def list_alerts(db: Session = Depends(get_db)):
    return [
        {
            "id": a.id,
            "parameter": a.parameter,
            "condition": a.condition,
            "threshold": a.threshold,
            "label": a.label,
            "active": a.active,
        }
        for a in db.query(Alert).all()
    ]


@app.post("/api/alerts")
def create_alert(
    data: dict,
    key: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if key != API_KEY:
        return JSONResponse({"error": "invalid api key"}, status_code=401)
    alert = Alert(
        parameter=data["parameter"],
        condition=data["condition"],
        threshold=data["threshold"],
        label=data.get("label", ""),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return {"id": alert.id}


@app.delete("/api/alerts/{alert_id}")
def delete_alert(
    alert_id: int,
    key: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if key != API_KEY:
        return JSONResponse({"error": "invalid api key"}, status_code=401)
    db.query(Alert).filter(Alert.id == alert_id).delete()
    db.commit()
    return {"deleted": alert_id}
