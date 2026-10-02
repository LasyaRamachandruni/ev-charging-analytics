"""Publish the dbt marts from DuckDB into Postgres for the API and web app.

DuckDB is the right engine for building the marts: columnar, in-process,
and fast on full scans. The web app has different needs. It makes many small
concurrent reads ("this site, this month, next 50 sessions"), and those
want a server database with B-tree indexes. So the marts are copied into Postgres
after each `dbt build`.

The copy never leaves the API looking at half-loaded tables. Everything is
loaded and indexed in a staging schema, which then replaces `analytics`,
all in one transaction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

SCHEMA = "analytics"
STAGING = "analytics_staging"

# Per-site lookup the API needs on every request (timezone, date range).
# The DuckDB store runs the same query as a subquery.
SITES_SQL = """
    select site,
           any_value(timezone)          as timezone,
           min(local_date)              as first_date,
           max(local_date)              as last_date,
           count(*)                     as sessions,
           count(distinct station_id)   as stations,
           round(sum(kwh_delivered), 1) as energy_kwh
    from fct_charging_sessions
    group by site
"""

TABLES = {
    "fct_charging_sessions": "select * from fct_charging_sessions",
    "agg_daily_site": "select * from agg_daily_site",
    "agg_station_utilization": "select * from agg_station_utilization",
    "dim_stations": "select * from dim_stations",
    "fct_site_load_15min": "select * from fct_site_load_15min",
    "sites": SITES_SQL,
}

# Indexes match the API's access paths. See docs/QUERY_TUNING.md for how
# the sessions index was chosen.
INDEXES = [
    "alter table {s}.fct_charging_sessions add primary key (session_id)",
    "create index on {s}.fct_charging_sessions (site, connected_at_utc desc, session_id desc)",
    "create index on {s}.fct_charging_sessions (station_id, connected_at_utc desc, session_id desc)",
    "alter table {s}.agg_daily_site add primary key (site, local_date)",
    "alter table {s}.fct_site_load_15min add primary key (site, interval_start_utc)",
    "alter table {s}.agg_station_utilization add primary key (station_id)",
    "alter table {s}.dim_stations add primary key (station_id)",
    "alter table {s}.sites add primary key (site)",
]

PG_TYPES = {
    "VARCHAR": "text",
    "BIGINT": "bigint",
    "INTEGER": "integer",
    "DOUBLE": "double precision",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP WITH TIME ZONE": "timestamptz",
}


@dataclass
class PublishStats:
    tables: dict[str, int]
    seconds: float


def open_warehouse(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Read-only connection, unless this process already holds the file open read-write
    (DuckDB allows only one configuration per file per process)."""
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.ConnectionException:
        return duckdb.connect(str(path))


def _pg_type(duck_type: str) -> str:
    if duck_type.startswith("DECIMAL"):
        return "numeric"
    return PG_TYPES[duck_type]


def publish(warehouse: str | Path, dsn: str) -> PublishStats:
    import psycopg

    started = time.perf_counter()
    counts: dict[str, int] = {}
    duck = open_warehouse(warehouse)
    with psycopg.connect(dsn) as pg:
        with pg.cursor() as cur:
            cur.execute(f"drop schema if exists {STAGING} cascade")
            cur.execute(f"create schema {STAGING}")
            for name, sql in TABLES.items():
                rel = duck.sql(sql)
                cols = ", ".join(f'"{c}" {_pg_type(str(t))}' for c, t in zip(rel.columns, rel.types))
                cur.execute(f"create table {STAGING}.{name} ({cols})")
                rows = rel.fetchall()
                with cur.copy(f"copy {STAGING}.{name} from stdin") as copy:
                    for row in rows:
                        copy.write_row(row)
                counts[name] = len(rows)
            for stmt in INDEXES:
                cur.execute(stmt.format(s=STAGING))
            for name in TABLES:
                cur.execute(f"analyze {STAGING}.{name}")
            # Postgres DDL is transactional: until this commits, readers keep seeing the
            # old schema, and afterwards they see the complete new one.
            cur.execute(f"drop schema if exists {SCHEMA} cascade")
            cur.execute(f"alter schema {STAGING} rename to {SCHEMA}")
    duck.close()
    return PublishStats(counts, round(time.perf_counter() - started, 2))
