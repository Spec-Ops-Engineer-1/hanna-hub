from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Float, String, DateTime, Text
from database import Base


class Reading(Base):
    __tablename__ = "readings"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    ph = Column(Float, nullable=True)
    orp_mv = Column(Float, nullable=True)
    do_mgl = Column(Float, nullable=True)
    do_pct = Column(Float, nullable=True)
    ec_us = Column(Float, nullable=True)
    tds_mgl = Column(Float, nullable=True)
    temp_c = Column(Float, nullable=True)
    ise_value = Column(Float, nullable=True)
    ise_unit = Column(String(32), nullable=True)
    sample_id = Column(String(128), nullable=True)
    operator = Column(String(128), nullable=True)
    source = Column(String(32), default="manual")  # manual, csv, bridge


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    parameter = Column(String(32))
    condition = Column(String(8))  # gt, lt, eq
    threshold = Column(Float)
    label = Column(String(128))
    active = Column(Integer, default=1)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, index=True)
    reading_id = Column(Integer, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    value = Column(Float)
    message = Column(Text)
