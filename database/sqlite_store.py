"""
Stdlib-only SQLite persistence layer. This is the LOCAL/DEMO backend --
requires nothing beyond Python's standard library, so it runs anywhere,
including sandboxes with no network access to install Postgres/SQLAlchemy.

Production deployment (Docker) uses backend/app/db/models.py +
Postgres instead (see docker-compose.yml). This module exists so the
system is genuinely runnable end-to-end without that infrastructure, and
so `backend/app/main.py` has a real data source when DATABASE_URL points
at a local sqlite file (the .env.example default for local dev).

Schema here is a simplified subset of database/schema.sql sufficient for
the API endpoints currently implemented -- kept schema-compatible (same
column names/meaning) so nothing here contradicts the Postgres design.
"""
from __future__ import annotations

from collections import defaultdict
import sqlite3
from contextlib import contextmanager
from datetime import date

DDL = """
CREATE TABLE IF NOT EXISTS routes (
    route_id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    label TEXT,
    UNIQUE(origin, destination)
);

CREATE TABLE IF NOT EXISTS route_weights (
    route_id INTEGER NOT NULL,
    weight REAL NOT NULL,
    methodology_note TEXT,
    FOREIGN KEY(route_id) REFERENCES routes(route_id)
);

CREATE TABLE IF NOT EXISTS fare_observations (
    fare_id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id INTEGER NOT NULL,
    source TEXT,
    airline_code TEXT,
    travel_date TEXT NOT NULL,
    booking_date TEXT NOT NULL,
    departure_bucket TEXT NOT NULL,
    total_fare REAL NOT NULL,
    quality_status TEXT NOT NULL,
    data_mode TEXT NOT NULL,
    FOREIGN KEY(route_id) REFERENCES routes(route_id)
);
CREATE INDEX IF NOT EXISTS idx_fare_route_date ON fare_observations(route_id, booking_date);

CREATE TABLE IF NOT EXISTS index_values (
    index_date TEXT PRIMARY KEY,
    index_value REAL NOT NULL,
    data_mode TEXT NOT NULL,
    mom_change_pct REAL,
    wow_change_pct REAL,
    routes_included INTEGER NOT NULL,
    routes_excluded INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS route_index_values (
    route_id INTEGER NOT NULL,
    index_date TEXT NOT NULL,
    index_value REAL,
    avg_fare REAL,
    observation_count INTEGER,
    PRIMARY KEY (route_id, index_date)
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id INTEGER,
    booking_date TEXT,
    method TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    booking_date TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'ONLINE',
    last_success TEXT
);

CREATE TABLE IF NOT EXISTS airline_index_values (
    airline_code TEXT NOT NULL,
    index_date TEXT NOT NULL,
    avg_fare REAL,
    index_value REAL,
    observation_count INTEGER DEFAULT 0,
    PRIMARY KEY (airline_code, index_date)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,      -- 'national_index_move', 'route_index_move', 'source_failure', 'anomaly_rate'
    index_date TEXT NOT NULL,
    route_id INTEGER,
    message TEXT NOT NULL,
    threshold_pct REAL,
    actual_pct REAL,
    severity TEXT NOT NULL DEFAULT 'warning',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS index_revision_history (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_date TEXT NOT NULL,
    old_value REAL NOT NULL,
    new_value REAL NOT NULL,
    reason TEXT,
    revised_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS forecast_cache (
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (origin, destination)
);
"""


@contextmanager
def get_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(DDL)


def upsert_route(conn: sqlite3.Connection, origin: str, destination: str, label: str, weight: float) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO routes(origin, destination, label) VALUES (?,?,?)",
        (origin, destination, label),
    )
    row = conn.execute(
        "SELECT route_id FROM routes WHERE origin=? AND destination=?", (origin, destination)
    ).fetchone()
    route_id = row["route_id"]
    conn.execute("DELETE FROM route_weights WHERE route_id=?", (route_id,))
    conn.execute(
        "INSERT INTO route_weights(route_id, weight, methodology_note) VALUES (?,?,?)",
        (route_id, weight, "PROTOTYPE weight: DGCA traffic-share proxy, see METHODOLOGY.md"),
    )
    return route_id


def insert_fare_observations(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """INSERT INTO fare_observations
           (route_id, source, airline_code, travel_date, booking_date,
            departure_bucket, total_fare, quality_status, data_mode)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def upsert_index_value(conn: sqlite3.Connection, index_date: str, index_value: float,
                        data_mode: str, mom: float | None, wow: float | None,
                        included: int, excluded: int) -> None:
    conn.execute(
        """INSERT INTO index_values (index_date, index_value, data_mode, mom_change_pct,
                                      wow_change_pct, routes_included, routes_excluded)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(index_date) DO UPDATE SET
             index_value=excluded.index_value, mom_change_pct=excluded.mom_change_pct,
             wow_change_pct=excluded.wow_change_pct, routes_included=excluded.routes_included,
             routes_excluded=excluded.routes_excluded""",
        (index_date, index_value, data_mode, mom, wow, included, excluded),
    )


def upsert_route_index_value(conn: sqlite3.Connection, route_id: int, index_date: str,
                              index_value: float | None, avg_fare: float | None, count: int) -> None:
    conn.execute(
        """INSERT INTO route_index_values (route_id, index_date, index_value, avg_fare, observation_count)
           VALUES (?,?,?,?,?)
           ON CONFLICT(route_id, index_date) DO UPDATE SET
             index_value=excluded.index_value, avg_fare=excluded.avg_fare,
             observation_count=excluded.observation_count""",
        (route_id, index_date, index_value, avg_fare, count),
    )


# ---------- Query functions (mirror backend/app/db/queries.py's contract) ----------

def get_current_index(db_path: str) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM index_values ORDER BY index_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_index_history(db_path: str, start: str | None = None, end: str | None = None) -> list[dict]:
    q = "SELECT * FROM index_values WHERE 1=1"
    params = []
    if start:
        q += " AND index_date >= ?"; params.append(start)
    if end:
        q += " AND index_date <= ?"; params.append(end)
    q += " ORDER BY index_date"
    with get_conn(db_path) as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_routes(db_path: str) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """SELECT r.origin, r.destination, r.label, w.weight
               FROM routes r JOIN route_weights w ON r.route_id = w.route_id
               ORDER BY w.weight DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_route_analytics(
    db_path: str,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = 500,
    offset: int = 0,
) -> list[dict]:
    """Return one auditable row per route and airline for dashboard tables/export.

    `start`/`end` (YYYY-MM-DD, both optional) filter observations by their
    real `booking_date`. Kept in the fare_observations JOIN's ON clause
    (not WHERE) so routes with zero observations in the chosen window still
    appear instead of being silently dropped by the LEFT JOIN.

    `limit`/`offset` slice the final aggregated (route, airline) rows, never
    the raw joined observation rows -- slicing before aggregation would
    compute average/median/etc. from a partial, arbitrary subset of a
    group's observations. Pass limit=None for the full unpaginated list
    (used internally by get_analytics's per-route rollup).
    """
    date_filter_sql = ""
    date_params: list[str] = []
    if start:
        date_filter_sql += " AND fo.booking_date >= ?"
        date_params.append(start)
    if end:
        date_filter_sql += " AND fo.booking_date <= ?"
        date_params.append(end)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT r.origin, r.destination, r.label, w.weight,
                      fo.airline_code, fo.booking_date, fo.total_fare,
                      riv.index_value
               FROM routes r
               LEFT JOIN route_weights w ON w.route_id = r.route_id
               LEFT JOIN fare_observations fo ON fo.route_id = r.route_id
                    AND fo.quality_status='valid'{date_filter_sql}
               LEFT JOIN route_index_values riv ON riv.route_id = r.route_id
                    AND riv.index_date = (SELECT MAX(index_date) FROM route_index_values)
               ORDER BY w.weight DESC, r.origin, r.destination, fo.airline_code""",
            date_params,
        ).fetchall()
    grouped = {}
    for row in rows:
        key = (row["origin"], row["destination"], row["airline_code"] or "ALL")
        item = grouped.setdefault(key, {
            "origin": row["origin"], "destination": row["destination"],
            "label": row["label"] or f"{row['origin']}-{row['destination']}",
            "weight": row["weight"], "airline_code": row["airline_code"] or "ALL",
            "fares": [], "dates": set(), "latest_index": row["index_value"],
        })
        if row["total_fare"] is not None:
            item["fares"].append(float(row["total_fare"]))
        if row["booking_date"]:
            item["dates"].add(row["booking_date"])
    output = []
    for item in grouped.values():
        fares = sorted(item.pop("fares"))
        item.pop("dates")
        if not fares:
            continue
        n = len(fares)
        median = fares[n // 2] if n % 2 else (fares[n // 2 - 1] + fares[n // 2]) / 2
        item.update({
            "observation_count": n,
            "average_fare": round(sum(fares) / n, 2),
            "median_fare": round(median, 2),
            "lowest_fare": round(fares[0], 2),
            "highest_fare": round(fares[-1], 2),
        })
        output.append(item)
    if limit is not None:
        output = output[offset:offset + limit]
    return output


def get_analytics(
    db_path: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    # Unpaginated (limit=None): the rollup below needs every airline row for
    # a route to compute that route's average correctly, regardless of how
    # many routes the caller ultimately wants back.
    routes = get_route_analytics(db_path, start, end, limit=None)
    route_rollup = {}
    for row in routes:
        key = f"{row['origin']}-{row['destination']}"
        roll = route_rollup.setdefault(key, {"route": key, "label": row["label"], "fares": [], "index": row.get("latest_index")})
        roll["fares"].append(row["average_fare"])
    route_rows = []
    for row in route_rollup.values():
        row["average_fare"] = round(sum(row["fares"]) / len(row["fares"]), 2)
        row.pop("fares")
        route_rows.append(row)
    where_sql = "WHERE quality_status='valid'"
    where_params: list[str] = []
    if start:
        where_sql += " AND booking_date >= ?"
        where_params.append(start)
    if end:
        where_sql += " AND booking_date <= ?"
        where_params.append(end)
    with get_conn(db_path) as conn:
        buckets = conn.execute(
            f"""SELECT CASE WHEN total_fare < 3000 THEN '< Rs 3k'
                    WHEN total_fare < 6000 THEN 'Rs 3k-6k'
                    WHEN total_fare < 10000 THEN 'Rs 6k-10k'
                    WHEN total_fare < 15000 THEN 'Rs 10k-15k'
                    ELSE 'Rs 15k+' END AS bucket, COUNT(*) AS count
               FROM fare_observations {where_sql}
               GROUP BY bucket ORDER BY MIN(total_fare)""",
            where_params,
        ).fetchall()
        season = conn.execute(
            f"""SELECT substr(booking_date, 1, 7) AS month, AVG(total_fare) AS average_fare,
                      COUNT(*) AS observation_count
               FROM fare_observations {where_sql}
               GROUP BY month ORDER BY month""",
            where_params,
        ).fetchall()
    # route_count stays the total (unpaginated) route count; route_ranking
    # itself is paginated since it's the one list here that scales with
    # basket size.
    route_ranking = sorted(route_rows, key=lambda row: row["average_fare"])
    return {
        "route_ranking": route_ranking[offset:offset + limit],
        "fare_distribution": [dict(row) for row in buckets],
        "seasonality": [{**dict(row), "average_fare": round(row["average_fare"], 2)} for row in season],
        "route_count": len(route_rows),
    }


def get_observation_date_bounds(db_path: str) -> dict:
    """Real min/max booking_date across all valid fare observations, so the
    dashboard's date filters are bounded by dates that actually exist in the
    data instead of an assumed or invented range."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MIN(booking_date) AS min_date, MAX(booking_date) AS max_date "
            "FROM fare_observations WHERE quality_status='valid'"
        ).fetchone()
        return {"min": row["min_date"], "max": row["max_date"]}


def get_route_detail(db_path: str, origin: str, destination: str) -> dict | None:
    with get_conn(db_path) as conn:
        route = conn.execute(
            "SELECT route_id, label FROM routes WHERE origin=? AND destination=?",
            (origin, destination),
        ).fetchone()
        if not route:
            return None
        history = conn.execute(
            """SELECT index_date, index_value, avg_fare, observation_count
               FROM route_index_values WHERE route_id=? ORDER BY index_date""",
            (route["route_id"],),
        ).fetchall()
        return {"label": route["label"], "history": [dict(r) for r in history]}


def get_data_quality_summary(db_path: str) -> dict:
    with get_conn(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) c FROM fare_observations").fetchone()["c"]
        by_status = conn.execute(
            "SELECT quality_status, COUNT(*) c FROM fare_observations GROUP BY quality_status"
        ).fetchall()
        anomalies = conn.execute("SELECT COUNT(*) c FROM anomalies").fetchone()["c"]
        return {
            "total_observations": total,
            "by_quality_status": {r["quality_status"]: r["c"] for r in by_status},
            "anomaly_count": anomalies,
        }


def get_source_health(db_path: str) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM sources").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item["name"] == "mock":
                item["name"] = "airfare_observation_pipeline"
                item["label"] = "Local research backcast"
                item["status"] = "LOCAL_BACKCAST"
                item["data_mode"] = "simulated_backcast"
                item["note"] = "Local indexed observations retained for audit history; not a live scraper source."
            out.append(item)
        return out


def get_anomalies(db_path: str, limit: int = 50) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """SELECT a.anomaly_id, r.origin, r.destination, r.label, a.booking_date,
                      a.method, a.description
               FROM anomalies a JOIN routes r ON a.route_id = r.route_id
               ORDER BY a.booking_date DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_route_fare_history(db_path: str, origin: str, destination: str) -> list[dict]:
    """Raw valid fare observations for a route, used by /api/forecast to
    train on the fly (no separate forecast-storage table yet — see
    docs/LIMITATIONS.md)."""
    with get_conn(db_path) as conn:
        route = conn.execute(
            "SELECT route_id FROM routes WHERE origin=? AND destination=?", (origin, destination)
        ).fetchone()
        if not route:
            return []
        rows = conn.execute(
            """SELECT travel_date, booking_date, total_fare, departure_bucket
               FROM fare_observations
               WHERE route_id=? AND quality_status='valid'
               ORDER BY booking_date""",
            (route["route_id"],),
        ).fetchall()
        return [dict(r) for r in rows]


def get_top_movers(db_path: str, limit: int = 5) -> dict:
    """Top increasing/decreasing routes comparing the latest two index dates
    each route has data for."""
    with get_conn(db_path) as conn:
        latest_date = conn.execute("SELECT MAX(index_date) d FROM route_index_values").fetchone()["d"]
        if latest_date is None:
            return {"top_increasing": [], "top_decreasing": []}
        prev_date = conn.execute(
            "SELECT MAX(index_date) d FROM route_index_values WHERE index_date < ?", (latest_date,)
        ).fetchone()["d"]

        rows = conn.execute(
            """SELECT r.label, r.origin, r.destination,
                      cur.index_value AS current_value, prev.index_value AS prev_value
               FROM route_index_values cur
               JOIN route_index_values prev ON cur.route_id = prev.route_id AND prev.index_date = ?
               JOIN routes r ON r.route_id = cur.route_id
               WHERE cur.index_date = ? AND cur.index_value IS NOT NULL AND prev.index_value IS NOT NULL""",
            (prev_date, latest_date),
        ).fetchall()

        movers = []
        for r in rows:
            if r["prev_value"]:
                pct = 100.0 * (r["current_value"] - r["prev_value"]) / r["prev_value"]
                movers.append({**dict(r), "change_pct": round(pct, 3)})

        movers.sort(key=lambda x: x["change_pct"], reverse=True)
        return {
            "index_date": latest_date,
            "top_increasing": movers[:limit],
            "top_decreasing": sorted(movers, key=lambda x: x["change_pct"])[:limit],
        }


# ---------- Airline-level index ----------

def upsert_airline_index_value(conn: sqlite3.Connection, airline_code: str, index_date: str,
                                avg_fare: float | None, index_value: float | None, count: int) -> None:
    conn.execute(
        """INSERT INTO airline_index_values (airline_code, index_date, avg_fare, index_value, observation_count)
           VALUES (?,?,?,?,?)
           ON CONFLICT(airline_code, index_date) DO UPDATE SET
             avg_fare=excluded.avg_fare, index_value=excluded.index_value,
             observation_count=excluded.observation_count""",
        (airline_code, index_date, avg_fare, index_value, count),
    )


def get_airlines_summary(db_path: str) -> list[dict]:
    """Latest avg fare + index + route coverage + fare volatility per airline."""
    with get_conn(db_path) as conn:
        airlines = [r["airline_code"] for r in conn.execute(
            "SELECT DISTINCT airline_code FROM fare_observations WHERE airline_code IS NOT NULL"
        ).fetchall()]

        out = []
        for code in airlines:
            latest = conn.execute(
                """SELECT index_date, avg_fare, index_value, observation_count
                   FROM airline_index_values WHERE airline_code=? ORDER BY index_date DESC LIMIT 1""",
                (code,),
            ).fetchone()
            route_count = conn.execute(
                """SELECT COUNT(DISTINCT route_id) c FROM fare_observations
                   WHERE airline_code=? AND quality_status='valid'""",
                (code,),
            ).fetchone()["c"]
            volatility = conn.execute(
                """SELECT AVG(total_fare) avg_f,
                          (AVG(total_fare*total_fare) - AVG(total_fare)*AVG(total_fare)) var_f
                   FROM fare_observations WHERE airline_code=? AND quality_status='valid'""",
                (code,),
            ).fetchone()
            std = (volatility["var_f"] ** 0.5) if volatility["var_f"] and volatility["var_f"] > 0 else 0.0
            cov = (std / volatility["avg_f"]) if volatility["avg_f"] else None

            out.append({
                "airline_code": code,
                "latest_index_date": latest["index_date"] if latest else None,
                "latest_avg_fare": round(latest["avg_fare"], 2) if latest and latest["avg_fare"] else None,
                "latest_index_value": round(latest["index_value"], 2) if latest and latest["index_value"] else None,
                "route_coverage": route_count,
                "fare_volatility_coeff": round(cov, 4) if cov is not None else None,
            })
        out.sort(key=lambda x: x["latest_avg_fare"] or 0)
        return out


def get_airline_history(db_path: str, airline_code: str) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """SELECT index_date, avg_fare, index_value, observation_count
               FROM airline_index_values WHERE airline_code=? ORDER BY index_date""",
            (airline_code,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_airline_route_history(db_path: str, airline_code: str, top_n: int = 5) -> dict:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """SELECT r.origin, r.destination, r.label, fo.booking_date,
                      AVG(fo.total_fare) AS average_fare, COUNT(*) AS observation_count
               FROM fare_observations fo JOIN routes r ON r.route_id = fo.route_id
               WHERE fo.airline_code=? AND fo.quality_status='valid'
               GROUP BY r.route_id, fo.booking_date
               ORDER BY r.route_id, fo.booking_date""",
            (airline_code,),
        ).fetchall()
    route_counts = defaultdict(int)
    for row in rows:
        route_counts[f"{row['origin']}-{row['destination']}"] += row["observation_count"]
    selected = [route for route, _ in sorted(route_counts.items(), key=lambda item: item[1], reverse=True)[:top_n]]
    series = {route: [] for route in selected}
    labels = {}
    for row in rows:
        route = f"{row['origin']}-{row['destination']}"
        if route in series:
            labels[route] = row["label"] or route
            series[route].append({
                "date": row["booking_date"],
                "value": round(row["average_fare"], 2),
                "observation_count": row["observation_count"],
            })
    for points in series.values():
        points.sort(key=lambda point: point["date"])
    return {"airline_code": airline_code, "top_n": top_n, "routes": [{"route": route, "label": labels.get(route, route), "observation_count": route_counts[route], "series": points} for route, points in series.items()]}


def get_weekly_fare_overview(db_path: str) -> dict:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """SELECT booking_date, COALESCE(airline_code, 'UNKNOWN') AS airline_code,
                      AVG(total_fare) AS average_fare, COUNT(*) AS observation_count
               FROM fare_observations WHERE quality_status='valid'
               GROUP BY booking_date, airline_code ORDER BY booking_date"""
        ).fetchall()
    dates = sorted({row["booking_date"] for row in rows})[-7:]
    keep = set(dates)
    by_airline = defaultdict(list)
    for row in rows:
        if row["booking_date"] in keep:
            by_airline[row["airline_code"]].append({"date": row["booking_date"], "value": round(row["average_fare"], 2), "observation_count": row["observation_count"]})
    national = defaultdict(list)
    for row in rows:
        if row["booking_date"] in keep:
            national[row["booking_date"]].append(row["average_fare"])
    return {
        "date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
        "airlines": [{"route": code, "label": code, "series": points} for code, points in sorted(by_airline.items())],
        "national": [{"date": day, "value": round(sum(values) / len(values), 2)} for day, values in sorted(national.items())],
        "observation_count": sum(row["observation_count"] for row in rows if row["booking_date"] in keep),
    }


# ---------- Alerts ----------

def insert_alert(conn: sqlite3.Connection, alert_type: str, index_date: str, message: str,
                  threshold_pct: float | None, actual_pct: float | None,
                  route_id: int | None = None, severity: str = "warning") -> None:
    conn.execute(
        """INSERT INTO alerts (alert_type, index_date, route_id, message, threshold_pct, actual_pct, severity)
           VALUES (?,?,?,?,?,?,?)""",
        (alert_type, index_date, route_id, message, threshold_pct, actual_pct, severity),
    )


def get_alerts(db_path: str, limit: int = 50) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY index_date DESC, alert_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Revision history ----------

def record_revision(conn: sqlite3.Connection, index_date: str, old_value: float,
                     new_value: float, reason: str) -> None:
    conn.execute(
        "INSERT INTO index_revision_history (index_date, old_value, new_value, reason) VALUES (?,?,?,?)",
        (index_date, old_value, new_value, reason),
    )


def get_revision_history(db_path: str, limit: int = 50) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM index_revision_history ORDER BY revised_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Forecast cache ----------
# Forecast requests train two models on the fly (see backend/app/main.py's
# /api/forecast endpoint) -- expensive enough to be worth a short-lived
# cache rather than recomputing on every request. TTL is enforced by the
# caller (main.py checks `computed_at` age), not here.

def get_cached_forecast(db_path: str, origin: str, destination: str) -> dict | None:
    import json
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT computed_at, payload_json FROM forecast_cache WHERE origin=? AND destination=?",
            (origin, destination),
        ).fetchone()
        if not row:
            return None
        return {"computed_at": row["computed_at"], "payload": json.loads(row["payload_json"])}


def set_cached_forecast(db_path: str, origin: str, destination: str, payload: dict) -> None:
    import json
    from datetime import datetime
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO forecast_cache (origin, destination, computed_at, payload_json)
               VALUES (?,?,?,?)
               ON CONFLICT(origin, destination) DO UPDATE SET
                 computed_at=excluded.computed_at, payload_json=excluded.payload_json""",
            (origin, destination, datetime.utcnow().isoformat(), json.dumps(payload)),
        )
