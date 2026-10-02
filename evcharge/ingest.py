"""Raw JSONL -> bronze Parquet.

Every raw file under data/raw/<site>/ is parsed, flattened and validated:

- rows that break a rule in schema.problems() (or can't be parsed at all) go to
  data/bronze/quarantine.parquet with the reason, instead of silently disappearing;
- duplicate sessionIDs (the API can return a session twice across overlapping
  downloads) keep one copy;
- valid rows go to data/bronze/sessions/<site>.parquet.

Ingestion rebuilds bronze from all raw files each time, so it is idempotent: running
it twice gives the same result.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from .schema import COLUMNS, flatten, problems


@dataclass
class IngestStats:
    files: int = 0
    read: int = 0
    loaded: int = 0
    duplicates: int = 0
    quarantined: int = 0


TIME_COLUMNS = ["connection_time", "disconnect_time", "done_charging_time", "requested_departure"]


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    for col in TIME_COLUMNS:
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def ingest(raw_dir: str | Path = "data/raw", bronze_dir: str | Path = "data/bronze") -> IngestStats:
    raw_dir, bronze_dir = Path(raw_dir), Path(bronze_dir)
    stats = IngestStats()
    good: dict[str, list[dict]] = {}
    seen: set[str] = set()
    quarantine: list[dict] = []

    for path in sorted(raw_dir.glob("*/*.jsonl")):
        site = path.parent.name
        stats.files += 1
        with open(path) as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                stats.read += 1
                try:
                    raw = json.loads(line)
                    row = flatten(raw, site)
                except (KeyError, ValueError, TypeError) as exc:
                    quarantine.append({"site": site, "source": f"{path.name}:{line_no}",
                                       "session_id": None, "reason": f"unparseable: {exc}"})
                    continue
                issues = problems(row)
                if issues:
                    quarantine.append({"site": site, "source": f"{path.name}:{line_no}",
                                       "session_id": row["session_id"], "reason": ";".join(issues)})
                    continue
                if row["session_id"] in seen:
                    stats.duplicates += 1
                    continue
                seen.add(row["session_id"])
                good.setdefault(site, []).append(row)

    sessions_dir = bronze_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for old in sessions_dir.glob("*.parquet"):
        old.unlink()  # rebuild from raw each run
    for site, rows in good.items():
        _frame(rows).sort_values("connection_time").to_parquet(sessions_dir / f"{site}.parquet", index=False)
        stats.loaded += len(rows)

    stats.quarantined = len(quarantine)
    bronze_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(quarantine, columns=["site", "source", "session_id", "reason"]).to_parquet(
        bronze_dir / "quarantine.parquet", index=False
    )
    (bronze_dir / "ingest_stats.json").write_text(json.dumps(asdict(stats), indent=2))
    return stats
