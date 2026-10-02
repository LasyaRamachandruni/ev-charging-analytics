"""Benchmark for the session-log query, the API's heaviest access path.

    evcharge bench --dsn postgresql://... [--sites 20 --years 10]

It builds a large copy of the published sessions table in a `bench` schema by
repeating the real (or synthetic) sessions across many sites and shifted
periods. Then it times four versions of one request: "page 200 of a site's
sessions over one year, newest first, 50 per page". Each version changes one
thing from the one before it:

  1. naive:    no index; filter on cast(connected_at_local as date); OFFSET paging
  2. index:    add the composite index; query unchanged
  3. sargable: filter on a half-open UTC range the index can use; still OFFSET
  4. keyset:   replace OFFSET with a (connected_at_utc, session_id) cursor

The results go to stdout and to a JSON file, along with each plan's
EXPLAIN (ANALYZE, BUFFERS) output. docs/QUERY_TUNING.md is written from that file.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

COLUMNS = ("session_id, station_id, user_id, connected_at_utc, connected_at_local, connected_hours, "
           "charging_hours, kwh_delivered, kwh_requested, request_met")
PAGE = 50


def build(cur, sites: int, years: int) -> dict:
    """bench.sessions = analytics.fct_charging_sessions repeated over `sites` sites and `years` shifted periods."""
    cur.execute("select min(connected_at_utc), max(connected_at_utc), count(*) from analytics.fct_charging_sessions")
    lo, hi, base = cur.fetchone()
    span_days = (hi - lo).days + 1
    cur.execute("drop schema if exists bench cascade")
    cur.execute("create schema bench")
    cur.execute(f"""
        create table bench.sessions as
        select s.site_no, p.period,
               format('%s-%s-%s', f.session_id, s.site_no, p.period)   as session_id,
               format('site_%s', lpad(s.site_no::text, 3, '0'))         as site,
               format('%s-%s', f.station_id, s.site_no)                 as station_id,
               f.user_id,
               f.connected_at_utc   + p.period * interval '{span_days} days' as connected_at_utc,
               f.connected_at_local + p.period * interval '{span_days} days' as connected_at_local,
               f.connected_hours, f.charging_hours, f.kwh_delivered, f.kwh_requested, f.request_met
        from analytics.fct_charging_sessions f
        cross join generate_series(1, {sites}) as s(site_no)
        cross join generate_series(0, {years * 365 // span_days}) as p(period)
    """)
    cur.execute("alter table bench.sessions drop column site_no, drop column period")
    cur.execute("analyze bench.sessions")
    cur.execute("select count(*), pg_size_pretty(pg_total_relation_size('bench.sessions')) from bench.sessions")
    rows, size = cur.fetchone()
    cur.execute("select min(connected_at_local)::date, max(connected_at_local)::date from bench.sessions")
    first, last = cur.fetchone()
    return {"base_sessions": base, "rows": rows, "table_size": size, "sites": sites, "first": first, "last": last}


def queries(site: str, start: date, end: date, tz: str, cursor: tuple[datetime, str] | None,
            page: int) -> dict[str, tuple[str, dict]]:
    t0 = datetime.combine(start, datetime.min.time(), ZoneInfo(tz))
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), ZoneInfo(tz))
    order = "order by connected_at_utc desc, session_id desc"
    offset = (page - 1) * PAGE
    naive = (f"select {COLUMNS} from bench.sessions where site = %(site)s "
             f"and cast(connected_at_local as date) between %(start)s and %(end)s {order} offset {offset} limit {PAGE}")
    sargable = (f"select {COLUMNS} from bench.sessions where site = %(site)s "
                f"and connected_at_utc >= %(t0)s and connected_at_utc < %(t1)s {order} offset {offset} limit {PAGE}")
    keyset = (f"select {COLUMNS} from bench.sessions where site = %(site)s "
              f"and connected_at_utc >= %(t0)s and connected_at_utc < %(t1)s "
              f"and (connected_at_utc, session_id) < (%(cur_t)s, %(cur_id)s) {order} limit {PAGE}")
    p = {"site": site, "start": start, "end": end, "t0": t0, "t1": t1}
    if cursor:
        p["cur_t"], p["cur_id"] = cursor
    return {"naive": (naive, p), "index": (naive, p), "sargable": (sargable, p), "keyset": (keyset, p)}


def _time(cur, sql: str, params: dict, runs: int) -> dict:
    cur.execute(sql, params)  # warm the cache: every version is measured warm, like a busy API
    cur.fetchall()
    samples = []
    for _ in range(runs):
        t = time.perf_counter()
        cur.execute(sql, params)
        cur.fetchall()
        samples.append((time.perf_counter() - t) * 1000)
    cur.execute("explain (analyze, buffers, costs off, timing off, summary off) " + sql, params)
    plan = "\n".join(r[0] for r in cur.fetchall())
    return {"median_ms": round(statistics.median(samples), 2), "p95_ms": round(sorted(samples)[int(0.95 * runs) - 1], 2),
            "plan": plan}


def run(dsn: str, sites: int = 20, years: int = 10, runs: int = 30, page: int = 200, out: str | Path = "docs/query_tuning_results.json") -> dict:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as con, con.cursor() as cur:
        cur.execute("set max_parallel_workers_per_gather = 0")  # measure the plan, not core count
        info = build(cur, sites, years)
        cur.execute("select timezone from analytics.sites limit 1")
        tz = cur.fetchone()[0]
        site = f"site_{min(7, sites):03d}"
        end = info["last"] - timedelta(days=30)
        start = end - timedelta(days=364)

        base_sql, base_p = queries(site, start, end, tz, None, page)["sargable"]
        where = base_sql.split(" where ", 1)[1].split(" order by")[0]
        cur.execute(f"select count(*) from bench.sessions where {where}", base_p)
        matching = cur.fetchone()[0]
        # the keyset cursor for page 200 is the last row of page 199
        cur.execute(f"select connected_at_utc, session_id from bench.sessions where {where} "
                    f"order by connected_at_utc desc, session_id desc offset {(page - 1) * PAGE - 1} limit 1", base_p)
        cursor = cur.fetchone()
        if cursor is None:
            raise ValueError(f"only {matching} sessions match; page {page} doesn't exist. Use more --sites/--years")

        qs = queries(site, start, end, tz, cursor, page)
        results = {}
        results["naive"] = _time(cur, *qs["naive"], runs)
        cur.execute("create index sessions_site_time on bench.sessions (site, connected_at_utc desc, session_id desc)")
        cur.execute("analyze bench.sessions")
        for name in ("index", "sargable", "keyset"):
            results[name] = _time(cur, *qs[name], runs)

        pages = {}
        for name in ("naive", "index", "sargable", "keyset"):
            cur.execute(*qs[name])
            pages[name] = [r[0] for r in cur.fetchall()]
        assert pages["naive"] == pages["index"] == pages["sargable"] == pages["keyset"], "versions disagree"

        cur.execute("select pg_size_pretty(pg_relation_size('bench.sessions_site_time'))")
        index_size = cur.fetchone()[0]
        cur.execute("select version()")
        version = cur.fetchone()[0].split(",")[0]
        cur.execute("drop schema bench cascade")

    report = {"postgres": version, **{k: str(v) for k, v in info.items()}, "index_size": index_size,
              "request": {"site": site, "start": str(start), "end": str(end), "matching_sessions": matching,
                          "page": page, "page_size": PAGE},
              "runs": runs, "results": results}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(report, indent=2) + "\n")
    return report
