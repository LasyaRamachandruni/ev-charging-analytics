"""Read access to the marts for the API, backed by Postgres or DuckDB.

Every query is written once in SQL that both engines accept, with `:name`
parameters. Postgres is the production backend (see publish.py). DuckDB
reads the warehouse file directly, which needs no infrastructure for
development and tests.

All date filters are turned into half-open UTC ranges on `connected_at_utc`
or `interval_start_utc`, never `cast(... as date) = ...`, so they can use the
indexes. See docs/QUERY_TUNING.md.
"""

from __future__ import annotations

import base64
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .publish import SCHEMA, SITES_SQL, open_warehouse

_PARAM = re.compile(r"(?<!:):([a-z_][a-z0-9_]*)")

SESSION_COLUMNS = """
    session_id, station_id, user_id, connected_at_utc, connected_at_local, disconnected_at_utc,
    connected_hours, charging_hours, idle_hours, kwh_delivered, kwh_requested, request_met, overstayed
"""


class NotFound(LookupError):
    pass


class Store:
    dialect = ""

    # -- engine plumbing ---------------------------------------------------
    def _rows(self, sql: str, params: dict[str, Any]) -> list[dict]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def ping(self) -> bool:
        return self._rows("select 1 as ok", {})[0]["ok"] == 1

    # -- helpers -----------------------------------------------------------
    def site(self, site: str) -> dict:
        rows = self._rows("select * from sites where site = :site", {"site": site})
        if not rows:
            raise NotFound(f"unknown site {site!r}")
        return rows[0]

    def _utc_range(self, site: dict, start: date | None, end: date | None) -> tuple[datetime, datetime, date, date]:
        """Local calendar days [start, end] -> UTC instants [t0, t1)."""
        start = start or site["first_date"]
        end = end or site["last_date"]
        if end < start:
            raise ValueError("end is before start")
        tz = ZoneInfo(site["timezone"])
        t0 = datetime.combine(start, time.min, tz)
        t1 = datetime.combine(end + timedelta(days=1), time.min, tz)
        return t0, t1, start, end

    # -- queries -----------------------------------------------------------
    def sites(self) -> list[dict]:
        return self._rows("select * from sites order by site", {})

    def summary(self, site: str, start: date | None = None, end: date | None = None) -> dict:
        s = self.site(site)
        t0, t1, start, end = self._utc_range(s, start, end)
        p = {"site": site, "t0": t0, "t1": t1}
        row = self._rows("""
            select count(*)                                                           as sessions,
                   count(distinct user_id)                                            as identified_users,
                   coalesce(sum(kwh_delivered), 0)                                    as energy_kwh,
                   avg(connected_hours)                                               as avg_connected_hours,
                   avg(idle_hours)                                                    as avg_idle_hours,
                   avg(case when request_met then 1.0 when has_request then 0.0 end) as share_requests_met,
                   avg(case when overstayed then 1.0 else 0.0 end)                    as share_overstayed
            from fct_charging_sessions
            where site = :site and connected_at_utc >= :t0 and connected_at_utc < :t1
        """, p)[0]
        peak = self._rows("""
            select interval_start_local, power_kw
            from fct_site_load_15min
            where site = :site and interval_start_utc >= :t0 and interval_start_utc < :t1
            order by power_kw desc
            limit 1
        """, p)
        return {"site": site, "start": start, "end": end, "days": (end - start).days + 1, **row,
                "peak_kw": peak[0]["power_kw"] if peak else None,
                "peak_at": peak[0]["interval_start_local"] if peak else None}

    def daily(self, site: str, start: date | None = None, end: date | None = None) -> list[dict]:
        s = self.site(site)
        _, _, start, end = self._utc_range(s, start, end)
        return self._rows("""
            select * from agg_daily_site
            where site = :site and local_date >= :start and local_date <= :end
            order by local_date
        """, {"site": site, "start": start, "end": end})

    def load_profile(self, site: str, start: date | None = None, end: date | None = None) -> dict:
        """Average and peak site power by local time of day, weekdays vs weekends.

        Averages divide by the number of calendar days of each kind in the range,
        since intervals with no charging are absent from the load table and count as 0 kW.
        """
        s = self.site(site)
        t0, t1, start, end = self._utc_range(s, start, end)
        rows = self._rows("""
            select extract(isodow from interval_start_local) >= 6                                  as is_weekend,
                   cast(extract(hour from interval_start_local) * 4
                        + floor(extract(minute from interval_start_local) / 15) as integer)     as slot,
                   sum(power_kw)                                                               as total_kw,
                   max(power_kw)                                                               as max_kw
            from fct_site_load_15min
            where site = :site and interval_start_utc >= :t0 and interval_start_utc < :t1
            group by 1, 2
        """, {"site": site, "t0": t0, "t1": t1})
        days = [start + timedelta(d) for d in range((end - start).days + 1)]
        n = {False: sum(d.isoweekday() < 6 for d in days), True: sum(d.isoweekday() >= 6 for d in days)}
        by = {(r["is_weekend"], r["slot"]): r for r in rows}
        out = []
        for slot in range(96):
            item = {"slot": slot, "time": f"{slot // 4:02d}:{slot % 4 * 15:02d}"}
            for weekend, key in ((False, "weekday"), (True, "weekend")):
                r = by.get((weekend, slot))
                item[f"{key}_avg_kw"] = round(float(r["total_kw"]) / n[weekend], 3) if r and n[weekend] else 0.0
                item[f"{key}_max_kw"] = round(float(r["max_kw"]), 3) if r else 0.0
            out.append(item)
        return {"site": site, "start": start, "end": end, "weekdays": n[False], "weekend_days": n[True],
                "slots": out}

    def stations(self, site: str) -> list[dict]:
        self.site(site)
        return self._rows("""
            select u.station_id, d.space_id, u.sessions, d.total_kwh, u.occupied_share, u.charging_share,
                   u.idle_share_of_occupied, d.first_session_date, d.last_session_date
            from agg_station_utilization u
            join dim_stations d on d.station_id = u.station_id
            where u.site = :site
            order by u.occupied_share desc, u.station_id
        """, {"site": site})

    def sessions(self, site: str, start: date | None = None, end: date | None = None,
                 station: str | None = None, limit: int = 50, cursor: str | None = None) -> dict:
        """One page of sessions, newest first, with keyset pagination.

        The cursor is the (connected_at_utc, session_id) of the last row of the previous
        page, so a page costs the same at page 1 and at page 1,000. With OFFSET, the database
        would read and throw away every earlier row.
        """
        s = self.site(site)
        t0, t1, start, end = self._utc_range(s, start, end)
        p: dict[str, Any] = {"site": site, "t0": t0, "t1": t1, "limit": limit + 1}
        where = ["site = :site", "connected_at_utc >= :t0", "connected_at_utc < :t1"]
        if station:
            where.append("station_id = :station")
            p["station"] = station
        if cursor:
            p["cur_t"], p["cur_id"] = decode_cursor(cursor)
            where.append("(connected_at_utc, session_id) < (:cur_t, :cur_id)")
        rows = self._rows(f"""
            select {SESSION_COLUMNS}
            from fct_charging_sessions
            where {' and '.join(where)}
            order by connected_at_utc desc, session_id desc
            limit :limit
        """, p)
        more = len(rows) > limit
        rows = rows[:limit]
        nxt = encode_cursor(rows[-1]["connected_at_utc"], rows[-1]["session_id"]) if more else None
        return {"site": site, "start": start, "end": end, "items": rows, "next_cursor": nxt}


def encode_cursor(t: datetime, session_id: str) -> str:
    t = t.astimezone(timezone.utc)  # same cursor whichever engine produced the row
    return base64.urlsafe_b64encode(f"{t.isoformat()}|{session_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        t, sid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(t), sid
    except Exception as exc:
        raise ValueError("invalid cursor") from exc


class PostgresStore(Store):
    dialect = "postgres"

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(
            dsn, min_size=min_size, max_size=max_size, open=True,
            kwargs={"row_factory": dict_row, "options": f"-c search_path={SCHEMA}", "autocommit": True},
        )

    def _rows(self, sql, params):
        with self.pool.connection() as con:
            return con.execute(_PARAM.sub(r"%(\1)s", sql), params).fetchall()

    def close(self):
        self.pool.close()


class DuckDBStore(Store):
    dialect = "duckdb"

    def __init__(self, warehouse: str | Path):
        if not Path(warehouse).exists():
            raise FileNotFoundError(f"{warehouse} not found; run `evcharge run` first")
        self.con = open_warehouse(warehouse)

    def _rows(self, sql, params):
        if re.search(r"\bfrom sites\b", sql):  # Postgres has a `sites` table; here it is computed
            sql = f"with sites as ({SITES_SQL}) {sql}"
        cur = self.con.cursor()  # one cursor per query: DuckDB connections are not shared across threads
        try:
            rel = cur.execute(_PARAM.sub(r"$\1", sql), params)
            cols = [d[0] for d in rel.description]
            return [dict(zip(cols, r)) for r in rel.fetchall()]
        finally:
            cur.close()

    def close(self):
        self.con.close()
