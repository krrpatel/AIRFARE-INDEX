"""
SQLAlchemy ORM models mirroring database/schema.sql exactly. This module is
the single source of truth for column names/types used by queries.py and
the pipeline loader — schema.sql and this file must be kept in sync
manually (documented here since we intentionally avoided an ORM-migration
framework to keep infra minimal, per the project's "don't overengineer"
constraint).

NOTE: requires `sqlalchemy` + `psycopg2-binary` (see requirements.txt).
Not exercised in the dev sandbox used to build this repo (no network to
install packages there) — validated by static review against schema.sql
column-for-column. Runs inside the `backend` Docker image, which does have
network access at build time.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
    Integer, Numeric, SmallInteger, String, Time, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Airport(Base):
    __tablename__ = "airports"
    iata_code = Column(String(3), primary_key=True)
    name = Column(String, nullable=False)
    city = Column(String, nullable=False)
    state = Column(String)
    latitude = Column(Numeric)
    longitude = Column(Numeric)


class Airline(Base):
    __tablename__ = "airlines"
    airline_code = Column(String(10), primary_key=True)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class Route(Base):
    __tablename__ = "routes"
    route_id = Column(Integer, primary_key=True)
    origin = Column(String(3), ForeignKey("airports.iata_code"), nullable=False)
    destination = Column(String(3), ForeignKey("airports.iata_code"), nullable=False)
    is_in_basket = Column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint("origin", "destination"),)


class RouteWeight(Base):
    __tablename__ = "route_weights"
    route_id = Column(Integer, ForeignKey("routes.route_id"), primary_key=True)
    effective_from = Column(Date, primary_key=True)
    weight = Column(Numeric(8, 6), nullable=False)
    methodology_note = Column(String)


class Source(Base):
    __tablename__ = "sources"
    source_id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    source_type = Column(String(20), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)
    robots_checked_at = Column(DateTime(timezone=True))
    robots_allowed = Column(Boolean)


class FareObservation(Base):
    __tablename__ = "fare_observations"
    fare_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_id = Column(UUID(as_uuid=True))
    source_id = Column(Integer, ForeignKey("sources.source_id"), nullable=False)
    airline_code = Column(String(10), ForeignKey("airlines.airline_code"))
    flight_number = Column(String)
    route_id = Column(Integer, ForeignKey("routes.route_id"), nullable=False)
    booking_ts = Column(DateTime(timezone=True), nullable=False)
    travel_date = Column(Date, nullable=False)
    departure_time = Column(Time)
    arrival_time = Column(Time)
    duration_minutes = Column(Integer)
    stops = Column(SmallInteger, nullable=False, default=0)
    cabin_class = Column(String(20), nullable=False, default="Economy")
    base_fare = Column(Numeric(10, 2), nullable=False)
    taxes = Column(Numeric(10, 2), nullable=False, default=0)
    total_fare = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="INR")
    fare_type = Column(String)
    days_to_departure = Column(Integer, nullable=False)
    departure_bucket = Column(String(4), nullable=False)
    data_mode = Column(String(20), nullable=False, default="live")
    quality_status = Column(String(20), nullable=False, default="valid")
    is_duplicate = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class IndexValue(Base):
    __tablename__ = "index_values"
    index_date = Column(Date, primary_key=True)
    index_value = Column(Numeric(10, 4), nullable=False)
    data_mode = Column(String(20), nullable=False)
    mom_change_pct = Column(Numeric(6, 3))
    wow_change_pct = Column(Numeric(6, 3))
    yoy_change_pct = Column(Numeric(6, 3))
    routes_included = Column(Integer, nullable=False)
    routes_imputed = Column(Integer, nullable=False, default=0)
    computed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    revised_at = Column(DateTime(timezone=True))


class RouteIndexValue(Base):
    __tablename__ = "route_index_values"
    route_id = Column(Integer, ForeignKey("routes.route_id"), primary_key=True)
    index_date = Column(Date, primary_key=True)
    index_value = Column(Numeric(10, 4), nullable=False)
    avg_fare = Column(Numeric(10, 2))
    observation_count = Column(Integer, nullable=False, default=0)
    is_imputed = Column(Boolean, nullable=False, default=False)


class DataQualityEvent(Base):
    __tablename__ = "data_quality_events"
    event_id = Column(Integer, primary_key=True)
    fare_id = Column(UUID(as_uuid=True))
    raw_id = Column(UUID(as_uuid=True))
    event_type = Column(String(30), nullable=False)
    severity = Column(String(10), nullable=False)
    detail = Column(String)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Anomaly(Base):
    __tablename__ = "anomalies"
    anomaly_id = Column(Integer, primary_key=True)
    fare_id = Column(UUID(as_uuid=True))
    route_id = Column(Integer, ForeignKey("routes.route_id"))
    detected_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    method = Column(String(30), nullable=False)
    score = Column(Numeric)
    description = Column(String)
    resolved = Column(Boolean, nullable=False, default=False)
