# EV Charging Analytics

A batch data pipeline for EV charging sessions from Caltech's public [ACN-Data](https://ev.caltech.edu/dataset) network: ingestion from the API, validation with quarantine, dbt models on DuckDB with data tests, a 15-minute site load curve, orchestration with Airflow, and a report on how chargers are actually used.

```
ACN-Data API ──► raw JSONL ──► ingest ──► bronze Parquet ──► dbt build (DuckDB) ──► report
                (immutable)     │          (valid rows)       staging → marts        charts + summary
                                └──► quarantine (bad rows + reason)   26 data tests
                                                    Airflow: daily fetch → ingest → dbt build → report
```

## Questions it answers

- **When does the site draw the most power?** A 15-minute load curve built from session data, for demand-charge and capacity planning.
- **How much charger capacity is wasted?** For each station, the time spent charging versus the time occupied by a car that has already finished charging.
- **Do drivers get the energy they ask for?** The share of app requests fully met, by how long the car stayed plugged in.
- **Daily operations:** sessions, energy, drivers, plug-in durations and overstays, per site per day.

## Pipeline

| Stage | What it does |
|---|---|
| `evcharge fetch` | Pulls sessions from the ACN-Data API (token auth, pagination, retries with backoff) and writes them unchanged to `data/raw/<site>/*.jsonl`. A download only becomes visible when complete. |
| `evcharge ingest` | Flattens each record (keeping the driver's latest app request), validates it, drops duplicate session IDs and writes `data/bronze/sessions/<site>.parquet`. Rows that fail a rule go to `quarantine.parquet` with the reason instead of disappearing. Ingestion is rebuilt from raw each run, so it is idempotent. |
| `evcharge transform` | `dbt build`: one staging view and five mart tables, plus 26 data tests. |
| `evcharge report` | Three charts and a summary table from the marts. |

**Validity rules** (rows that break one are quarantined): disconnect before connect, negative energy, charging finished outside the session, average power above what Level-2 hardware can deliver, sessions longer than 72 hours.

### dbt models

| Model | Grain | Purpose |
|---|---|---|
| `stg_sessions` | session | Durations, local time, idle time, request fulfillment |
| `fct_charging_sessions` | session | Adds outcome flags: request met, overstayed |
| `dim_stations` | station | First and last use, sessions, energy |
| `agg_daily_site` | site × day | Sessions, energy, drivers, durations, service levels |
| `agg_station_utilization` | station | Shares of time occupied, charging and idle |
| `fct_site_load_15min` | site × 15 min | Power demand: each session's energy spread over its charging window |

**Data tests** include uniqueness and not-null keys, value ranges, `charging time ≤ plugged-in time`, `charging share ≤ occupied share`, and an **energy-conservation test**: the 15-minute load curve must add back up to the kWh actually delivered. Breaking the load model on purpose (losing 10% of energy) makes that test fail.

## Running it

```bash
pip install -e ".[dev]"

# real data: free token from https://ev.caltech.edu/register
export ACN_API_TOKEN=...
evcharge fetch --site caltech --start 2019-01-01 --end 2019-07-01
evcharge run                      # ingest -> dbt build -> report into results/

# no token: synthetic sessions in the same raw format
evcharge run --synthetic --inject-issues
```

`--inject-issues` adds malformed and duplicate records, to show them being quarantined and dropped. Results go to `results/`; the DuckDB warehouse is `data/warehouse.duckdb`, which you can query directly:

```bash
duckdb data/warehouse.duckdb "select * from agg_daily_site order by local_date limit 10"
```

**Airflow:** `dags/ev_charging_pipeline.py` runs fetch → ingest → dbt build → report daily for the previous day. A failed data test stops the run before the report is regenerated.

## Example output

> ⚠️ **The images below come from the synthetic generator**, not real ACN-Data. They show what the pipeline produces; they are not findings. Real results are produced by `evcharge fetch` + `evcharge run`.

![Load profile](docs/demo/load_profile.png)
![Station utilization](docs/demo/station_utilization.png)
![Request outcomes](docs/demo/request_outcomes.png)

## Tests

```bash
pytest
```

20 tests cover timestamp parsing, flattening, the validity rules, API pagination and retries (against a fake HTTP client), the synthetic generator, ingestion (quarantine, dedupe across overlapping downloads, idempotency) and the full pipeline end to end, including every dbt data test. CI runs them on each push, plus a full synthetic run.

## Design notes

- **Raw is immutable; bronze is rebuilt.** Re-running ingestion never double-counts, and a bug fix in validation can be applied to all history by re-ingesting.
- **Quarantine, not drop.** Bad records stay visible with a reason, so data issues can be counted and traced to their source file and line.
- **Load from session data.** The API also offers per-minute charging time series, which would give exact load. Spreading session energy evenly over the charging window is the standard approximation when only session records are available, and the conservation test guarantees no energy is lost or invented.
- **DuckDB + dbt.** The same models would run on a warehouse such as Snowflake or BigQuery by changing the dbt profile.

## Project layout

```
evcharge/
  sources/acn.py        ACN-Data API client
  sources/synthetic.py  synthetic sessions (same raw format)
  schema.py             flattening and validity rules
  ingest.py             raw -> bronze, quarantine, dedupe
  report.py             charts and summary
  cli.py                evcharge command
dbt/                    staging and mart models, data tests
dags/                   Airflow DAG
tests/                  pytest suite
```

## Data

ACN-Data: Lee, Li and Low, *"ACN-Data: Analysis and Applications of an Open EV Charging Dataset"*, ACM e-Energy 2019. Data from the Caltech, JPL and office001 sites, available through the [ACN-Data API](https://ev.caltech.edu/dataset).
