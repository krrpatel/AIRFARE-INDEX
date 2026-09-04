-- Airfare Price Index — PostgreSQL Schema
-- Design notes: every table maps to a stage in the pipeline or index engine.
-- Timestamps are timestamptz throughout (never naive) since sources may be
-- scraped from servers in different timezones.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- REFERENCE DATA
-- ============================================================

CREATE TABLE airports (
    iata_code       CHAR(3) PRIMARY KEY,
    name            TEXT NOT NULL,
    city            TEXT NOT NULL,
    state           TEXT,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION
);

CREATE TABLE airlines (
    airline_code    VARCHAR(10) PRIMARY KEY,   -- e.g. '6E', 'AI', 'QP'
    name            TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE routes (
    route_id        SERIAL PRIMARY KEY,
    origin          CHAR(3) NOT NULL REFERENCES airports(iata_code),
    destination     CHAR(3) NOT NULL REFERENCES airports(iata_code),
    is_in_basket    BOOLEAN NOT NULL DEFAULT FALSE,  -- prototype index basket membership
    basket_version  TEXT,
    dgca_pair_route TEXT,
    UNIQUE(origin, destination)
);

CREATE TABLE route_weights (
    route_id        INTEGER NOT NULL REFERENCES routes(route_id),
    effective_from  DATE NOT NULL,
    weight          NUMERIC(8,6) NOT NULL CHECK (weight >= 0),
    methodology_note TEXT,   -- e.g. 'DGCA traffic-share proxy, prototype basket'
    PRIMARY KEY (route_id, effective_from)
);

CREATE TABLE holidays (
    holiday_date    DATE PRIMARY KEY,
    name            TEXT NOT NULL,
    is_national     BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE sources (
    source_id       SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,       -- 'indigo', 'airindia', 'makemytrip', 'mock'
    source_type     VARCHAR(20) NOT NULL CHECK (source_type IN ('airline','ota','mock')),
    is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    robots_checked_at TIMESTAMPTZ,
    robots_allowed  BOOLEAN
);

-- ============================================================
-- RAW / STAGING LAYER  (append-only, never mutated)
-- ============================================================

CREATE TABLE raw_fare_observations (
    raw_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id       INTEGER NOT NULL REFERENCES sources(source_id),
    scraped_at      TIMESTAMPTZ NOT NULL,
    raw_payload     JSONB NOT NULL,       -- untouched adapter output
    scraping_run_id UUID NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_raw_fare_scraped_at ON raw_fare_observations(scraped_at);
CREATE INDEX idx_raw_fare_run ON raw_fare_observations(scraping_run_id);

CREATE TABLE scraping_runs (
    scraping_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id       INTEGER NOT NULL REFERENCES sources(source_id),
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(20) NOT NULL CHECK (status IN ('running','success','partial','failed')),
    observations_collected INTEGER DEFAULT 0,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0
);

-- ============================================================
-- CLEAN LAYER
-- ============================================================

CREATE TABLE fare_observations (
    fare_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_id          UUID REFERENCES raw_fare_observations(raw_id),
    source_id       INTEGER NOT NULL REFERENCES sources(source_id),
    airline_code    VARCHAR(10) REFERENCES airlines(airline_code),
    flight_number   TEXT,
    route_id        INTEGER NOT NULL REFERENCES routes(route_id),
    booking_ts      TIMESTAMPTZ NOT NULL,      -- when the price was observed
    travel_date     DATE NOT NULL,
    departure_time  TIME,
    arrival_time    TIME,
    duration_minutes INTEGER,
    stops           SMALLINT NOT NULL DEFAULT 0,
    cabin_class     VARCHAR(20) NOT NULL DEFAULT 'Economy',
    availability_status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE'
                        CHECK (availability_status IN ('AVAILABLE','SOLD_OUT','CANCELLED','NOT_FOUND','SOURCE_ERROR','UNKNOWN')),
    base_fare       NUMERIC(10,2) CHECK (base_fare >= 0),
    taxes           NUMERIC(10,2) CHECK (taxes >= 0),
    airport_charges NUMERIC(10,2) CHECK (airport_charges >= 0),
    udf             NUMERIC(10,2) CHECK (udf >= 0),
    psf_asf         NUMERIC(10,2) CHECK (psf_asf >= 0),
    fuel_surcharge  NUMERIC(10,2) CHECK (fuel_surcharge >= 0),
    convenience_fee NUMERIC(10,2) CHECK (convenience_fee >= 0),
    service_fee     NUMERIC(10,2) CHECK (service_fee >= 0),
    other_mandatory_charges NUMERIC(10,2) CHECK (other_mandatory_charges >= 0),
    published_fare  NUMERIC(10,2) CHECK (published_fare >= 0),
    offered_fare    NUMERIC(10,2) CHECK (offered_fare >= 0),
    total_fare      NUMERIC(10,2) NOT NULL CHECK (total_fare >= 0),
    source_native_fare JSONB,
    currency        CHAR(3) NOT NULL DEFAULT 'INR',
    fare_type       TEXT,
    days_to_departure INTEGER NOT NULL,
    departure_bucket VARCHAR(4) NOT NULL,      -- B1..B6, see METHODOLOGY.md
    data_mode       VARCHAR(20) NOT NULL DEFAULT 'live'
                        CHECK (data_mode IN ('live','mock','simulated_backcast')),
    quality_status  VARCHAR(20) NOT NULL DEFAULT 'valid'
                        CHECK (quality_status IN ('valid','suspicious','rejected')),
    is_duplicate    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fare_route_date ON fare_observations(route_id, travel_date);
CREATE INDEX idx_fare_booking_ts ON fare_observations(booking_ts);
CREATE INDEX idx_fare_quality ON fare_observations(quality_status);
CREATE UNIQUE INDEX uq_fare_dedup ON fare_observations
    (source_id, route_id, travel_date, cabin_class, COALESCE(flight_number, ''), booking_ts)
    WHERE is_duplicate = FALSE;

CREATE TABLE data_quality_events (
    event_id        SERIAL PRIMARY KEY,
    fare_id         UUID REFERENCES fare_observations(fare_id),
    raw_id          UUID REFERENCES raw_fare_observations(raw_id),
    event_type      VARCHAR(30) NOT NULL,   -- 'invalid_airport','negative_fare','duplicate','outlier_iqr','outlier_isoforest', etc.
    severity        VARCHAR(10) NOT NULL CHECK (severity IN ('info','warning','error')),
    detail          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_dq_events_type ON data_quality_events(event_type);

CREATE TABLE anomalies (
    anomaly_id      SERIAL PRIMARY KEY,
    fare_id         UUID REFERENCES fare_observations(fare_id),
    route_id        INTEGER REFERENCES routes(route_id),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    method          VARCHAR(30) NOT NULL,   -- 'iqr','zscore','isolation_forest'
    score           NUMERIC,
    description     TEXT,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE
);

-- ============================================================
-- INDEX ENGINE OUTPUT LAYER
-- ============================================================

CREATE TABLE index_values (
    index_date      DATE PRIMARY KEY,
    index_value     NUMERIC(10,4) NOT NULL,
    data_mode       VARCHAR(20) NOT NULL CHECK (data_mode IN ('live','simulated_backcast')),
    mom_change_pct  NUMERIC(6,3),
    wow_change_pct  NUMERIC(6,3),
    yoy_change_pct  NUMERIC(6,3),
    routes_included INTEGER NOT NULL,
    routes_imputed  INTEGER NOT NULL DEFAULT 0,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    revised_at      TIMESTAMPTZ
);

CREATE TABLE index_revision_history (
    revision_id     SERIAL PRIMARY KEY,
    index_date      DATE NOT NULL REFERENCES index_values(index_date),
    old_value       NUMERIC(10,4) NOT NULL,
    new_value       NUMERIC(10,4) NOT NULL,
    reason          TEXT,
    revised_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE route_index_values (
    route_id        INTEGER NOT NULL REFERENCES routes(route_id),
    index_date      DATE NOT NULL,
    index_value     NUMERIC(10,4) NOT NULL,
    avg_fare        NUMERIC(10,2),
    observation_count INTEGER NOT NULL DEFAULT 0,
    is_imputed      BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (route_id, index_date)
);

CREATE TABLE airline_index_values (
    airline_code    VARCHAR(10) NOT NULL REFERENCES airlines(airline_code),
    index_date      DATE NOT NULL,
    avg_fare        NUMERIC(10,2),
    index_value     NUMERIC(10,4),
    observation_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (airline_code, index_date)
);

-- ============================================================
-- ML LAYER
-- ============================================================

CREATE TABLE forecasts (
    forecast_id     SERIAL PRIMARY KEY,
    route_id        INTEGER NOT NULL REFERENCES routes(route_id),
    target_date     DATE NOT NULL,
    predicted_fare  NUMERIC(10,2) NOT NULL,
    lower_bound     NUMERIC(10,2),
    upper_bound     NUMERIC(10,2),
    model_name      TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_forecast_route_target ON forecasts(route_id, target_date);

COMMENT ON TABLE fare_observations IS 'Clean layer: one row per valid/suspicious fare observation, after parsing/normalization/validation from raw_fare_observations.';
COMMENT ON TABLE index_values IS 'One row per calendar day: the national Airfare Price Index, per docs/METHODOLOGY.md.';
COMMENT ON COLUMN fare_observations.departure_bucket IS 'Elementary-aggregate bucket (B1-B6) controlling for days-to-departure, per METHODOLOGY.md section 2.';
