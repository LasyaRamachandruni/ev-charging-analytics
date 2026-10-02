"""Download raw charging sessions from the ACN-Data API (https://ev.caltech.edu).

Get a free API token by signing up at https://ev.caltech.edu/register, then:

    export ACN_API_TOKEN=...
    evcharge fetch --site caltech --start 2019-01-01 --end 2019-12-31

Sessions are written untouched, one JSON object per line, to
data/raw/<site>/<start>_<end>.jsonl, so ingestion can be re-run without
calling the API again.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import requests

from ..schema import format_time

API_URL = "https://ev.caltech.edu/api/v1/"
SITES = ("caltech", "jpl", "office001")
PAGE_SIZE = 100


class ACNClient:
    def __init__(self, token: str | None = None, url: str = API_URL, session: requests.Session | None = None):
        self.token = token or os.environ.get("ACN_API_TOKEN")
        if not self.token:
            raise RuntimeError("set ACN_API_TOKEN (free token from https://ev.caltech.edu/register)")
        self.url = url
        self.http = session or requests.Session()

    def _get(self, url: str, params: dict | None = None, retries: int = 4) -> dict:
        for attempt in range(retries):
            resp = self.http.get(url, params=params, auth=(self.token, ""), timeout=60)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")

    def sessions(self, site: str, start: datetime, end: datetime) -> Iterator[dict]:
        """Yield raw sessions that connected in [start, end), oldest first, following pagination."""
        if site not in SITES:
            raise ValueError(f"site must be one of {SITES}")
        where = f'connectionTime>="{format_time(start)}" and connectionTime<"{format_time(end)}"'
        url = f"{self.url}sessions/{site}"
        params: dict | None = {"where": where, "sort": "connectionTime", "max_results": PAGE_SIZE}
        while url:
            payload = self._get(url, params)
            yield from payload.get("_items", [])
            nxt = payload.get("_links", {}).get("next")
            # the "next" link is relative to the API root and already carries the query
            url, params = (self.url + nxt["href"], None) if nxt else (None, None)


def _as_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def fetch_to_file(site: str, start: date, end: date, out_dir: str | Path = "data/raw", client: ACNClient | None = None) -> Path:
    client = client or ACNClient()
    out = Path(out_dir) / site / f"{start.isoformat()}_{end.isoformat()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".jsonl.part")
    n = 0
    with open(tmp, "w") as f:
        for s in client.sessions(site, _as_utc(start), _as_utc(end)):
            f.write(json.dumps(s, default=str) + "\n")
            n += 1
    tmp.replace(out)  # only a complete download becomes visible to ingestion
    print(f"fetched {n} sessions for {site} -> {out}")
    return out
