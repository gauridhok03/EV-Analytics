-- EV Charging Analytics: PostgreSQL schema
-- All timestamps are naive local time of the station (no timezone conversion).
-- Sample rows carry is_simulated = TRUE.

CREATE TABLE IF NOT EXISTS tariffs (
    tariff_id       SERIAL PRIMARY KEY,
    tariff_name     VARCHAR(60)  NOT NULL UNIQUE,
    price_per_kwh   NUMERIC(8,2) NOT NULL CHECK (price_per_kwh >= 0),
    peak_start_hour SMALLINT     CHECK (peak_start_hour BETWEEN 0 AND 23),
    peak_end_hour   SMALLINT     CHECK (peak_end_hour BETWEEN 1 AND 24),
    peak_multiplier NUMERIC(4,2) NOT NULL DEFAULT 1.00 CHECK (peak_multiplier >= 1),
    currency        CHAR(3)      NOT NULL DEFAULT 'INR',
    CONSTRAINT ck_tariff_peak_window CHECK (
        (peak_start_hour IS NULL AND peak_end_hour IS NULL)
        OR (peak_start_hour IS NOT NULL AND peak_end_hour IS NOT NULL
            AND peak_end_hour > peak_start_hour)
    )
);

CREATE TABLE IF NOT EXISTS stations (
    station_id      VARCHAR(10)  PRIMARY KEY,
    station_name    VARCHAR(100) NOT NULL,
    city            VARCHAR(60)  NOT NULL,
    address         VARCHAR(200),
    latitude        NUMERIC(9,6) NOT NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude       NUMERIC(9,6) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    status          VARCHAR(20)  NOT NULL DEFAULT 'Operational'
                    CHECK (status IN ('Operational','Maintenance','Offline')),
    tariff_id       INTEGER      NOT NULL REFERENCES tariffs(tariff_id) ON DELETE RESTRICT,
    commissioned_on DATE,
    is_simulated    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chargers (
    charger_id     VARCHAR(20)  PRIMARY KEY,
    station_id     VARCHAR(10)  NOT NULL REFERENCES stations(station_id) ON DELETE RESTRICT,
    charger_type   VARCHAR(20)  NOT NULL
                   CHECK (charger_type IN ('AC Level 2','DC Fast','DC Ultra-Fast')),
    connector_type VARCHAR(20)  NOT NULL
                   CHECK (connector_type IN ('Type 2','CCS2','CHAdeMO','GB/T')),
    power_kw       NUMERIC(6,1) NOT NULL CHECK (power_kw > 0),
    status         VARCHAR(20)  NOT NULL DEFAULT 'Operational'
                   CHECK (status IN ('Operational','Maintenance','Offline')),
    is_simulated   BOOLEAN      NOT NULL DEFAULT TRUE,
    -- lets child tables guarantee "this charger belongs to this station"
    CONSTRAINT uq_charger_station UNIQUE (charger_id, station_id)
);

-- session_id is deliberately NOT unique: duplicate IDs must be storable so the
-- validation module can detect and report them. record_id is the technical key.
-- start_ts / end_ts are nullable (missing events); *_raw keeps unparseable input.
-- Energy and cost carry no sign constraint so negative values can be reviewed.
CREATE TABLE IF NOT EXISTS charging_sessions (
    record_id       SERIAL PRIMARY KEY,
    session_id      VARCHAR(20)   NOT NULL,
    charger_id      VARCHAR(20)   NOT NULL,
    station_id      VARCHAR(10)   NOT NULL,
    start_ts        TIMESTAMP,
    end_ts          TIMESTAMP,
    start_raw       TEXT,
    end_raw         TEXT,
    energy_kwh      NUMERIC(10,3),
    session_status  VARCHAR(20)   NOT NULL DEFAULT 'Completed'
                    CHECK (session_status IN ('Completed','Interrupted','In Progress','Failed')),
    charging_cost   NUMERIC(10,2),
    is_simulated    BOOLEAN       NOT NULL DEFAULT TRUE,
    injected_issue  VARCHAR(40),
    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP     NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_session_charger_station FOREIGN KEY (charger_id, station_id)
        REFERENCES chargers (charger_id, station_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS energy_records (
    energy_record_id  SERIAL PRIMARY KEY,
    session_record_id INTEGER       NOT NULL
                      REFERENCES charging_sessions(record_id) ON DELETE CASCADE,
    charger_id        VARCHAR(20)   NOT NULL REFERENCES chargers(charger_id) ON DELETE RESTRICT,
    recorded_at       TIMESTAMP     NOT NULL,
    energy_kwh        NUMERIC(10,3) NOT NULL CHECK (energy_kwh >= 0),
    power_kw          NUMERIC(6,1)  CHECK (power_kw >= 0)
);

-- end_ts NULL means the outage is still ongoing.
CREATE TABLE IF NOT EXISTS outage_events (
    outage_id    SERIAL PRIMARY KEY,
    charger_id   VARCHAR(20) NOT NULL,
    station_id   VARCHAR(10) NOT NULL,
    start_ts     TIMESTAMP   NOT NULL,
    end_ts       TIMESTAMP,
    outage_type  VARCHAR(30) NOT NULL
                 CHECK (outage_type IN ('Hardware fault','Network outage','Power supply',
                                        'Connector damage','Scheduled maintenance')),
    is_planned   BOOLEAN     NOT NULL DEFAULT FALSE,
    description  TEXT,
    is_simulated BOOLEAN     NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_outage_order CHECK (end_ts IS NULL OR end_ts > start_ts),
    CONSTRAINT fk_outage_charger_station FOREIGN KEY (charger_id, station_id)
        REFERENCES chargers (charger_id, station_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_chargers_station        ON chargers (station_id);
CREATE INDEX IF NOT EXISTS ix_stations_tariff         ON stations (tariff_id);
CREATE INDEX IF NOT EXISTS ix_sessions_charger_start  ON charging_sessions (charger_id, start_ts);
CREATE INDEX IF NOT EXISTS ix_sessions_station_start  ON charging_sessions (station_id, start_ts);
CREATE INDEX IF NOT EXISTS ix_sessions_session_id     ON charging_sessions (session_id);
CREATE INDEX IF NOT EXISTS ix_sessions_start          ON charging_sessions (start_ts);
CREATE INDEX IF NOT EXISTS ix_energy_session          ON energy_records (session_record_id);
CREATE INDEX IF NOT EXISTS ix_energy_charger_time     ON energy_records (charger_id, recorded_at);
CREATE INDEX IF NOT EXISTS ix_outage_charger_start    ON outage_events (charger_id, start_ts);
CREATE INDEX IF NOT EXISTS ix_outage_station_start    ON outage_events (station_id, start_ts);
