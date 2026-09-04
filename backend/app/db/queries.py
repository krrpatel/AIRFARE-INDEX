"""
Query functions backing the FastAPI endpoints in backend/app/main.py.
Kept separate from main.py so routing/HTTP concerns stay independent of
SQL — makes both layers independently testable.
"""
from datetime import date, timedelta

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from backend.app.db.models import IndexValue, Route, RouteIndexValue, Source


def get_current_index(db: Session) -> IndexValue | None:
    return db.query(IndexValue).order_by(desc(IndexValue.index_date)).first()


def get_index_history(db: Session, start: date | None, end: date | None) -> list[IndexValue]:
    q = db.query(IndexValue)
    if start:
        q = q.filter(IndexValue.index_date >= start)
    if end:
        q = q.filter(IndexValue.index_date <= end)
    return q.order_by(IndexValue.index_date).all()


def get_route_index_history(db: Session, route_id: int, days: int = 90) -> list[RouteIndexValue]:
    since = date.today() - timedelta(days=days)
    return (
        db.query(RouteIndexValue)
        .filter(RouteIndexValue.route_id == route_id, RouteIndexValue.index_date >= since)
        .order_by(RouteIndexValue.index_date)
        .all()
    )


def get_route_by_codes(db: Session, origin: str, destination: str) -> Route | None:
    return (
        db.query(Route)
        .filter(Route.origin == origin.upper(), Route.destination == destination.upper())
        .first()
    )


def get_source_health(db: Session) -> list[Source]:
    return db.query(Source).order_by(Source.name).all()


def get_top_movers(db: Session, index_date: date, limit: int = 5) -> tuple[list, list]:
    """Returns (top_increasing, top_decreasing) route index rows for a given day,
    compared against the previous day's value for the same route."""
    today_rows = (
        db.query(RouteIndexValue)
        .filter(RouteIndexValue.index_date == index_date)
        .all()
    )
    prev_date = index_date - timedelta(days=1)
    prev_lookup = {
        r.route_id: r.index_value
        for r in db.query(RouteIndexValue).filter(RouteIndexValue.index_date == prev_date).all()
    }

    changes = []
    for row in today_rows:
        prev = prev_lookup.get(row.route_id)
        if prev and prev != 0:
            pct = float(100.0 * (row.index_value - prev) / prev)
            changes.append((row, pct))

    changes.sort(key=lambda x: x[1], reverse=True)
    top_increasing = changes[:limit]
    top_decreasing = sorted(changes, key=lambda x: x[1])[:limit]
    return top_increasing, top_decreasing
