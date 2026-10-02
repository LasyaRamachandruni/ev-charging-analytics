"""Command line entry point.

    evcharge fetch --site caltech --start 2019-01-01 --end 2019-12-31   # real data (needs ACN_API_TOKEN)
    evcharge ingest                                                    # raw -> bronze
    evcharge transform                                                 # dbt build (models + tests)
    evcharge report                                                    # charts + summary
    evcharge run                                                       # ingest + transform + report

    evcharge run --synthetic   # generate synthetic data first (no token needed)

    evcharge publish --dsn postgresql://...   # copy the marts into Postgres for the API
    evcharge serve                            # API + web app (Postgres if EVCHARGE_PG_DSN is set)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = ROOT / "dbt"


def _paths(args) -> tuple[Path, Path, Path]:
    data = Path(args.data_dir).resolve()
    return data / "raw", data / "bronze", data / "warehouse.duckdb"


def cmd_fetch(args) -> int:
    from .sources.acn import fetch_to_file

    raw, _, _ = _paths(args)
    fetch_to_file(args.site, date.fromisoformat(args.start), date.fromisoformat(args.end), raw)
    return 0


def cmd_synth(args) -> int:
    from .sources.synthetic import generate, write_jsonl

    raw, _, _ = _paths(args)
    start = date.fromisoformat(args.start)
    sessions = generate(start, args.days, seed=args.seed, inject_issues=args.inject_issues)
    path = write_jsonl(sessions, raw / "synthetic" / f"{start.isoformat()}_{args.days}d.jsonl")
    print(f"wrote {len(sessions)} synthetic sessions -> {path}")
    return 0


def cmd_ingest(args) -> int:
    from .ingest import ingest

    raw, bronze, _ = _paths(args)
    stats = ingest(raw, bronze)
    print(f"ingested {stats.loaded} sessions from {stats.files} file(s); "
          f"{stats.duplicates} duplicates dropped, {stats.quarantined} quarantined")
    return 0 if stats.loaded else 1


def cmd_transform(args) -> int:
    from dbt.cli.main import dbtRunner

    _, bronze, warehouse = _paths(args)
    os.environ["EVCHARGE_BRONZE"] = str(bronze)
    os.environ["EVCHARGE_WAREHOUSE"] = str(warehouse)
    result = dbtRunner().invoke(["build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR), "--quiet"])
    print("dbt build: " + ("passed" if result.success else "FAILED"))
    return 0 if result.success else 1


def cmd_report(args) -> int:
    from .report import make_report

    _, _, warehouse = _paths(args)
    label = args.label or ("Synthetic data (pipeline demo)" if args.synthetic else "Caltech ACN-Data")
    for p in make_report(warehouse, args.out, label):
        print(f"wrote {p}")
    return 0


def cmd_run(args) -> int:
    if args.synthetic:
        cmd_synth(args)
    for step in (cmd_ingest, cmd_transform, cmd_report):
        if step(args) != 0:
            return 1
    return 0


def _dsn(args) -> str:
    dsn = args.dsn or os.environ.get("EVCHARGE_PG_DSN")
    if not dsn:
        sys.exit("pass --dsn or set EVCHARGE_PG_DSN")
    return dsn


def cmd_publish(args) -> int:
    from .serving.publish import publish

    _, _, warehouse = _paths(args)
    stats = publish(warehouse, _dsn(args))
    print(f"published {sum(stats.tables.values())} rows in {len(stats.tables)} tables in {stats.seconds}s")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    _, _, warehouse = _paths(args)
    os.environ.setdefault("EVCHARGE_WAREHOUSE", str(warehouse))
    uvicorn.run("evcharge.api:app", host=args.host, port=args.port)
    return 0


def cmd_bench(args) -> int:
    from .bench import run

    report = run(_dsn(args), sites=args.sites, years=args.years, runs=args.runs, out=args.out)
    print(f"{report['rows']} rows ({report['table_size']}); request matches {report['request']['matching_sessions']} sessions")
    for name, r in report["results"].items():
        print(f"  {name:9s} median {r['median_ms']:8.2f} ms   p95 {r['p95_ms']:8.2f} ms")
    return 0


def cmd_openapi(args) -> int:
    import json

    from fastapi.openapi.utils import get_openapi

    from .api import create_app

    app = create_app(store=object())  # the schema doesn't need a database
    spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
    Path(args.out).write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evcharge", description="EV charging session pipeline")
    p.add_argument("--data-dir", default="data", help="where raw/, bronze/ and the warehouse live")
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="download sessions from the ACN-Data API")
    f.add_argument("--site", default="caltech", choices=["caltech", "jpl", "office001"])
    f.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    f.add_argument("--end", required=True, help="YYYY-MM-DD, exclusive")
    f.set_defaults(fn=cmd_fetch)

    def synth_args(sp):
        sp.add_argument("--start", default="2019-01-07")
        sp.add_argument("--days", type=int, default=56)
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--inject-issues", action="store_true", help="add bad records to exercise data quality")

    s = sub.add_parser("synth", help="generate synthetic sessions (no token needed)")
    synth_args(s)
    s.set_defaults(fn=cmd_synth)

    sub.add_parser("ingest", help="raw JSONL -> bronze Parquet").set_defaults(fn=cmd_ingest)
    sub.add_parser("transform", help="dbt build: models and data tests").set_defaults(fn=cmd_transform)

    def report_args(sp):
        sp.add_argument("--out", default="results")
        sp.add_argument("--label", default="", help="data label shown on charts")

    r = sub.add_parser("report", help="charts and summary from the marts")
    report_args(r)
    r.add_argument("--synthetic", action="store_true", help="label the report as synthetic data")
    r.set_defaults(fn=cmd_report)

    run = sub.add_parser("run", help="ingest + transform + report")
    run.add_argument("--synthetic", action="store_true", help="generate synthetic data first")
    synth_args(run)
    report_args(run)
    run.set_defaults(fn=cmd_run)

    pub = sub.add_parser("publish", help="copy the marts from DuckDB into Postgres")
    pub.add_argument("--dsn", help="Postgres connection string (default: $EVCHARGE_PG_DSN)")
    pub.set_defaults(fn=cmd_publish)

    sv = sub.add_parser("serve", help="run the API and web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)

    b = sub.add_parser("bench", help="benchmark the session-log query on a scaled-up copy in Postgres")
    b.add_argument("--dsn", help="Postgres connection string (default: $EVCHARGE_PG_DSN)")
    b.add_argument("--sites", type=int, default=20)
    b.add_argument("--years", type=int, default=10)
    b.add_argument("--runs", type=int, default=30)
    b.add_argument("--out", default="docs/query_tuning_results.json")
    b.set_defaults(fn=cmd_bench)

    oa = sub.add_parser("openapi", help="write the API schema (the web app's types are generated from it)")
    oa.add_argument("--out", default="web/openapi.json")
    oa.set_defaults(fn=cmd_openapi)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
