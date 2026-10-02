"""The REST API on the DuckDB store, and parity with Postgres when one is available.

Postgres tests run when EVCHARGE_TEST_PG_DSN is set (CI starts a Postgres
service for them); otherwise they are skipped.
"""

import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from evcharge.api import create_app  # noqa: E402
from evcharge.cli import main  # noqa: E402
from evcharge.serving.store import DuckDBStore, encode_cursor  # noqa: E402

PG_DSN = os.environ.get("EVCHARGE_TEST_PG_DSN")


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    root = tmp_path_factory.mktemp("api")
    assert main(["--data-dir", str(root / "data"), "run", "--synthetic", "--days", "42",
                 "--out", str(root / "results")]) == 0
    return root / "data" / "warehouse.duckdb"


@pytest.fixture(scope="module")
def client(warehouse):
    with TestClient(create_app(DuckDBStore(warehouse))) as c:
        yield c


@pytest.fixture(scope="module")
def site(client):
    return client.get("/api/sites").json()[0]


def test_health_and_sites(client, site):
    assert client.get("/api/health").json() == {"status": "ok", "backend": "duckdb"}
    assert site["sessions"] > 0 and site["timezone"] == "America/Los_Angeles"


def test_summary_agrees_with_daily_rows(client, site):
    s = client.get(f"/api/sites/{site['site']}/summary", params={"start": "2019-01-14", "end": "2019-01-27"}).json()
    daily = client.get(f"/api/sites/{site['site']}/daily", params={"start": "2019-01-14", "end": "2019-01-27"}).json()
    assert s["days"] == len(daily) == 14
    assert s["sessions"] == sum(d["sessions"] for d in daily)
    assert s["energy_kwh"] == pytest.approx(sum(d["energy_kwh"] for d in daily), abs=0.05)
    assert s["peak_kw"] > 0


def test_load_profile_conserves_energy(client, site):
    lp = client.get(f"/api/sites/{site['site']}/load-profile").json()
    assert len(lp["slots"]) == 96 and lp["slots"][36]["time"] == "09:00"
    energy = sum((x["weekday_avg_kw"] * lp["weekdays"] + x["weekend_avg_kw"] * lp["weekend_days"]) * 0.25
                 for x in lp["slots"])
    assert energy == pytest.approx(site["energy_kwh"], rel=0.002)
    peak_slot = max(lp["slots"], key=lambda x: x["weekday_avg_kw"])
    assert 7 <= int(peak_slot["time"][:2]) <= 12  # workplace site: morning peak


def test_stations(client, site):
    rows = client.get(f"/api/sites/{site['site']}/stations").json()
    assert len(rows) == site["stations"]
    assert all(0 <= r["charging_share"] <= r["occupied_share"] <= 1 for r in rows)
    assert [r["occupied_share"] for r in rows] == sorted((r["occupied_share"] for r in rows), reverse=True)


def _all_pages(client, site, **params):
    items, cursor, pages = [], None, 0
    while True:
        page = client.get(f"/api/sites/{site}/sessions", params={**params, **({"cursor": cursor} if cursor else {})})
        assert page.status_code == 200, page.text
        body = page.json()
        items += body["items"]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            return items, pages


def test_session_pages_cover_the_range_exactly_once(client, site):
    params = {"start": "2019-01-14", "end": "2019-01-20", "limit": 37}
    items, pages = _all_pages(client, site["site"], **params)
    total = client.get(f"/api/sites/{site['site']}/summary", params=params).json()["sessions"]
    ids = [i["session_id"] for i in items]
    assert len(ids) == len(set(ids)) == total and pages == -(-total // 37)
    keys = [(i["connected_at_utc"], i["session_id"]) for i in items]
    assert keys == sorted(keys, reverse=True)
    assert all("2019-01-14" <= i["connected_at_local"][:10] <= "2019-01-20" for i in items)


def test_session_station_filter(client, site):
    station = client.get(f"/api/sites/{site['site']}/stations").json()[0]
    items, _ = _all_pages(client, site["site"], station=station["station_id"], limit=500)
    assert len(items) == station["sessions"]
    assert {i["station_id"] for i in items} == {station["station_id"]}


def test_bad_requests(client, site):
    s = site["site"]
    assert client.get("/api/sites/nowhere/summary").status_code == 404
    assert client.get(f"/api/sites/{s}/daily", params={"start": "2019-02-01", "end": "2019-01-01"}).status_code == 422
    assert client.get(f"/api/sites/{s}/sessions", params={"cursor": "not-a-cursor"}).status_code == 422
    assert client.get(f"/api/sites/{s}/sessions", params={"limit": 5000}).status_code == 422
    assert client.get(f"/api/sites/{s}/daily", params={"start": "yesterday"}).status_code == 422


def test_openapi_documents_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/sites", "/api/sites/{site}/summary", "/api/sites/{site}/daily", "/api/sites/{site}/load-profile",
            "/api/sites/{site}/stations", "/api/sites/{site}/sessions"} <= set(paths)


# -- Postgres ----------------------------------------------------------------

pg = pytest.mark.skipif(not PG_DSN, reason="set EVCHARGE_TEST_PG_DSN to run Postgres tests")


@pg
def test_postgres_serves_the_same_answers(warehouse, client, site):
    from evcharge.serving.publish import publish
    from evcharge.serving.store import PostgresStore

    stats = publish(warehouse, PG_DSN)
    assert stats.tables["fct_charging_sessions"] == site["sessions"]
    publish(warehouse, PG_DSN)  # republishing replaces the schema cleanly

    s = site["site"]
    station = client.get(f"/api/sites/{s}/stations").json()[3]["station_id"]
    cursor = encode_cursor(__import__("datetime").datetime.fromisoformat("2019-01-25T18:00:00+00:00"), "~")
    urls = ["/api/sites", f"/api/sites/{s}/summary", f"/api/sites/{s}/daily?start=2019-01-10&end=2019-02-02",
            f"/api/sites/{s}/load-profile", f"/api/sites/{s}/stations",
            f"/api/sites/{s}/sessions?limit=200", f"/api/sites/{s}/sessions?station={station}&limit=20",
            f"/api/sites/{s}/sessions?cursor={cursor}&limit=50"]
    with TestClient(create_app(PostgresStore(PG_DSN))) as pgc:
        assert pgc.get("/api/health").json()["backend"] == "postgres"
        for url in urls:
            assert _rounded(pgc.get(url).json()) == _rounded(client.get(url).json()), url


def _rounded(x):
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, str) and x.endswith("Z"):  # timestamps serialize as ...Z
        return x
    return x


@pg
def test_bench_versions_return_the_same_page(warehouse, tmp_path):
    from evcharge.bench import run
    from evcharge.serving.publish import publish

    publish(warehouse, PG_DSN)
    report = run(PG_DSN, sites=2, years=3, runs=3, page=5, out=tmp_path / "bench.json")  # asserts the four pages agree
    assert set(report["results"]) == {"naive", "index", "sargable", "keyset"}
    assert "Index Scan using sessions_site_time" in report["results"]["keyset"]["plan"]
