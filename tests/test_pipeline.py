"""Ingestion and the full pipeline (ingest -> dbt build -> report) on synthetic data."""

import json
from datetime import date

import duckdb
import pandas as pd
import pytest

from evcharge.cli import main
from evcharge.ingest import ingest
from evcharge.sources.synthetic import generate, write_jsonl


def test_ingest_quarantines_bad_rows_and_dedupes(tmp_path):
    write_jsonl(generate(date(2019, 1, 7), 7, inject_issues=True), tmp_path / "raw/caltech/a.jsonl")
    stats = ingest(tmp_path / "raw", tmp_path / "bronze")

    assert stats.duplicates == 1
    assert stats.quarantined == 2
    q = pd.read_parquet(tmp_path / "bronze/quarantine.parquet")
    reasons = " ".join(q.reason)
    assert "disconnect_before_connect" in reasons and "kWhDelivered" in reasons
    sessions = pd.read_parquet(tmp_path / "bronze/sessions/caltech.parquet")
    assert len(sessions) == stats.loaded and sessions.session_id.is_unique
    assert str(sessions.connection_time.dt.tz) == "UTC"


def test_ingest_is_idempotent_and_dedupes_across_files(tmp_path):
    sessions = generate(date(2019, 1, 7), 7)
    write_jsonl(sessions, tmp_path / "raw/caltech/a.jsonl")
    write_jsonl(sessions[:50], tmp_path / "raw/caltech/b.jsonl")  # overlapping download
    first = ingest(tmp_path / "raw", tmp_path / "bronze")
    second = ingest(tmp_path / "raw", tmp_path / "bronze")
    assert first == second
    assert first.loaded == len(sessions) and first.duplicates == 50


def test_ingest_writes_stats(tmp_path):
    write_jsonl(generate(date(2019, 1, 7), 3), tmp_path / "raw/caltech/a.jsonl")
    stats = ingest(tmp_path / "raw", tmp_path / "bronze")
    saved = json.loads((tmp_path / "bronze/ingest_stats.json").read_text())
    assert saved["loaded"] == stats.loaded


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline")
    code = main(["--data-dir", str(root / "data"), "run", "--synthetic", "--days", "28",
                 "--inject-issues", "--out", str(root / "results")])
    assert code == 0, "pipeline (including dbt tests) should pass"
    return root


def test_pipeline_builds_marts(built):
    con = duckdb.connect(str(built / "data/warehouse.duckdb"))
    tables = {r[0] for r in con.sql("select table_name from information_schema.tables").fetchall()}
    assert {"fct_charging_sessions", "dim_stations", "agg_daily_site",
            "agg_station_utilization", "fct_site_load_15min"} <= tables

    delivered = con.sql("select sum(kwh_delivered) from fct_charging_sessions").fetchone()[0]
    spread = con.sql("select sum(energy_kwh) from fct_site_load_15min").fetchone()[0]
    assert spread == pytest.approx(delivered, rel=1e-3)

    # station occupancy can't exceed 100% of the time
    assert con.sql("select max(occupied_share) from agg_station_utilization").fetchone()[0] <= 1
    con.close()


def test_pipeline_writes_report(built):
    for name in ("load_profile.png", "station_utilization.png", "request_outcomes.png", "summary.md"):
        assert (built / "results" / name).stat().st_size > 0
    assert "Synthetic data" in (built / "results/summary.md").read_text()
