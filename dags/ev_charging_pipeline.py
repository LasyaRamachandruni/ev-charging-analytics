"""Airflow DAG: refresh charging data daily.

fetch yesterday's sessions -> ingest -> dbt build (models + data tests) -> report

Each task calls the `evcharge` CLI, so the same steps run identically by hand,
in CI and in Airflow. A failed dbt test fails the run before the report is
regenerated, so dashboards never show data that broke a quality check.

Set the Airflow Variable `evcharge_data_dir` (and ACN_API_TOKEN in the worker
environment) before enabling the DAG.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

DATA_DIR = "{{ var.value.get('evcharge_data_dir', '/opt/evcharge/data') }}"
SITE = "caltech"

with DAG(
    dag_id="ev_charging_pipeline",
    description="ACN-Data charging sessions: fetch, ingest, dbt build, report",
    start_date=datetime(2024, 1, 1),
    schedule="0 6 * * *",  # 06:00 UTC, after the previous day's sessions have closed
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["ev", "charging"],
) as dag:
    fetch = BashOperator(
        task_id="fetch",
        bash_command=f"evcharge --data-dir {DATA_DIR} fetch --site {SITE} --start {{{{ ds }}}} --end {{{{ next_ds }}}}",
    )
    ingest = BashOperator(task_id="ingest", bash_command=f"evcharge --data-dir {DATA_DIR} ingest")
    transform = BashOperator(task_id="dbt_build", bash_command=f"evcharge --data-dir {DATA_DIR} transform")
    report = BashOperator(
        task_id="report", bash_command=f"evcharge --data-dir {DATA_DIR} report --out {DATA_DIR}/results"
    )

    fetch >> ingest >> transform >> report
