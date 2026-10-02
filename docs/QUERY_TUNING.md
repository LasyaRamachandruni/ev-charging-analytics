# Tuning the session-log query

The session log in the web app is the API's heaviest read path. A planner picks a
site and a date range and pages back through every session, newest first. This
note walks through how that query went from 249 ms to 0.23 ms. Every number below
was measured with `evcharge bench`, and the raw results and plans are in
[`query_tuning_results.json`](query_tuning_results.json).

## Setup

- **Data.** The published sessions table, repeated across 20 sites and shifted
  across ten years: 2.9 million sessions, 447 MB. It's built from synthetic sessions,
  so the dates run to 2029. The shape is what matters: many sites, years of history,
  and about 14,000 sessions per site per year.
- **Request.** `site_007`, one year (Feb 14 2028 – Feb 12 2029, 14,341 sessions),
  **page 200** at 50 per page. That is roughly where someone scrolling back through a
  busy site's year ends up.
- **Measurement.** PostgreSQL 16 on a small cloud VM, parallel workers off so
  the plan, not the core count, decides the time. Each version gets 30 warm runs.
  Reported values are the median and the 95th percentile of the time to execute and fetch the page.
- **Correctness.** The benchmark checks that all four versions return exactly the
  same 50 rows.

## Results

| Version | What changed | Median | p95 | Buffers touched |
|---|---|---:|---:|---:|
| 1. Naive | – | 248.6 ms | 291.7 ms | 57,216 |
| 2. Index | composite index added, query unchanged | 31.4 ms | 32.8 ms | 9,029 |
| 3. Sargable | date filter rewritten as a UTC range | 8.9 ms | 9.3 ms | 5,080 |
| 4. Keyset | OFFSET replaced by a cursor | **0.23 ms** | 0.43 ms | **55** |

That is about 1,100× faster overall. The page now reads 55 buffers (8 kB pages)
where it used to read the entire table.

## What each step fixed

### 1. Naive: the whole table, every time

```sql
select ... from sessions
where site = 'site_007'
  and cast(connected_at_local as date) between '2028-02-14' and '2029-02-12'
order by connected_at_utc desc, session_id desc
offset 9950 limit 50
```

```
Limit
  ->  Sort  (rows=10000)  Sort Key: connected_at_utc DESC, session_id DESC
        ->  Seq Scan on sessions  (rows=14341)
              Rows Removed by Filter: 2903379
              Buffers: shared hit=3200 read=54016
```

There is no index, so Postgres reads all 2.9 million rows to find 14,341 of them.
Then it sorts those to throw away the first 9,950. The cost grows with the size of
the **whole table**, not with the size of the answer.

### 2. Add the composite index

```sql
create index on sessions (site, connected_at_utc desc, session_id desc);
```

The column order follows the query. `site` comes first because it is filtered with
equality. The time column comes next because it is both filtered by range and used
for ordering. `session_id` is last as a tiebreaker, so that the sort order (and the
cursor in step 4) is unique.

```
Bitmap Heap Scan on sessions  (rows=14341)
  Recheck Cond: (site = 'site_007')
  Filter: ((connected_at_local)::date >= ... AND (connected_at_local)::date <= ...)
  Rows Removed by Filter: 131545
  ->  Bitmap Index Scan on sessions_site_time  Index Cond: (site = 'site_007')  (rows=145886)
```

It's about 8× faster, but only `site` reaches the index. `cast(connected_at_local as date)`
wraps the column in a function, so the index can't apply the date range. Postgres
fetches all ten years for the site (145,886 rows) and then discards 90% of them.

### 3. Make the date filter sargable

The API turns the site's local calendar dates into a half-open UTC range using
the site's timezone. The column is then compared directly:

```sql
where site = 'site_007'
  and connected_at_utc >= '2028-02-14 08:00+00'   -- Feb 14, 00:00 Pacific
  and connected_at_utc <  '2029-02-13 08:00+00'   -- Feb 13, 00:00 Pacific (exclusive)
```

```
Bitmap Index Scan on sessions_site_time
  Index Cond: ((site = 'site_007') AND (connected_at_utc >= ...) AND (connected_at_utc < ...))
  (rows=14341)
```

Now the index returns exactly the matching rows, and nothing is discarded. The
half-open range `[start, next day)` is also correct across daylight-saving changes
and at fractional seconds, which `between ... and '23:59:59'` gets wrong.

One cost remains: `OFFSET 9950`. Postgres still produces and sorts 10,000 rows
just to return the last 50. Page 400 would cost twice as much as page 200.

### 4. Keyset pagination

Each page in the API's response carries a cursor: the `(connected_at_utc, session_id)`
of its last row. The next page asks for rows strictly before it:

```sql
where site = 'site_007'
  and connected_at_utc >= ... and connected_at_utc < ...
  and (connected_at_utc, session_id) < ('2028-06-05 14:35:46+00', '2_39_78_312_…')
order by connected_at_utc desc, session_id desc
limit 50
```

```
Limit
  ->  Index Scan using sessions_site_time  (rows=50)
        Index Cond: (... AND (ROW(connected_at_utc, session_id) < ROW(...)))
        Buffers: shared hit=55
```

The row comparison matches the index's column order. Postgres seeks straight to
the cursor and reads 50 entries in index order, with no sort and nothing skipped.
Page 200 now costs the same as page 1. It also stays correct while new sessions
arrive: OFFSET pages shift when rows are inserted ahead of them, but cursor pages don't.

The cost is that you can't jump to "page 137". For a log you scroll through, that's the
right trade, and it's why the web app shows **Show 50 more** rather than page numbers.

## Where this lives in the code

- The index is in `evcharge/serving/publish.py` (`INDEXES`), created on every publish.
- The query and cursor are in `evcharge/serving/store.py` (`Store.sessions`, `encode_cursor`).
- The local-date to UTC conversion is in `Store._utc_range`, and every endpoint uses it.
- Tests (`tests/test_api.py`) check two things: walking every page returns each
  session exactly once, in order; and Postgres and DuckDB return identical pages.

## Reproduce

```bash
evcharge run --synthetic --days 120
evcharge publish --dsn postgresql://...
evcharge bench --dsn postgresql://...      # about 25 s; writes docs/query_tuning_results.json
```

Absolute times depend on the machine, but the ratios between versions shouldn't.
