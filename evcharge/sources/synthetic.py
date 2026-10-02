"""Synthetic sessions in the exact raw ACN-Data format, for development, tests and CI.

The shapes follow what is typical for a workplace charging site like Caltech's:
arrivals peak around 8-9 am on weekdays, cars stay most of the working day, Level-2
stations deliver 3-7 kW, and many cars finish charging long before they leave
(idle time). About 60% of sessions carry an app request (kWh needed, expected
departure), and some requests are not fully met.

This is NOT real data. Results computed from it only show that the pipeline works;
real results come from `evcharge fetch` against the ACN-Data API.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..schema import format_time

TZ = "America/Los_Angeles"


def _station_ids(n: int) -> list[tuple[str, str]]:
    return [(f"2-39-{78 + i // 20}-{300 + i}", f"CA-{300 + i}") for i in range(n)]


def generate(
    start: date,
    days: int,
    stations: int = 54,
    users: int = 400,
    seed: int = 0,
    inject_issues: bool = False,
) -> list[dict]:
    """Return raw session dicts. With inject_issues=True a few malformed records and
    duplicates are added so data-quality handling can be exercised."""
    rng = random.Random(seed)
    tz = ZoneInfo(TZ)
    station_list = _station_ids(stations)
    sessions: list[dict] = []

    for d in range(days):
        day = start + timedelta(days=d)
        weekday = day.weekday() < 5
        expected = stations * (0.95 if weekday else 0.25)
        n = max(0, int(rng.gauss(expected, expected * 0.12)))
        free = station_list[:]
        rng.shuffle(free)
        for station_id, space_id in free[:n]:
            if weekday:
                arrive_h = rng.gauss(8.6, 1.3) if rng.random() < 0.85 else rng.gauss(17.5, 2.0)
                stay_h = max(0.4, rng.lognormvariate(1.85, 0.45))
            else:
                arrive_h = rng.gauss(12.0, 3.0)
                stay_h = max(0.3, rng.lognormvariate(1.0, 0.6))
            arrive_h = min(max(arrive_h, 0.0), 23.5)
            local = datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(hours=arrive_h)
            connect = local.astimezone(timezone.utc).replace(microsecond=0)
            disconnect = connect + timedelta(hours=stay_h)

            rate_kw = rng.choice([6.6, 6.6, 6.6, 3.3, 7.2])
            need_kwh = max(1.0, rng.gammavariate(2.2, 5.0))  # most cars top up 5-20 kWh
            charge_h = need_kwh / rate_kw
            if charge_h >= stay_h:  # left before finishing
                delivered = rate_kw * stay_h * rng.uniform(0.85, 1.0)
                done = disconnect - timedelta(minutes=rng.randint(0, 3))
            else:
                delivered = need_kwh * rng.uniform(0.97, 1.0)
                done = connect + timedelta(hours=charge_h)

            user_id = f"{rng.randint(1, users):09d}" if rng.random() < 0.7 else None
            user_inputs = None
            if user_id is not None and rng.random() < 0.85:
                requested = round(need_kwh * rng.uniform(0.9, 1.6), 1)
                minutes = int(stay_h * 60 * rng.uniform(0.8, 1.2))
                user_inputs = [{
                    "userID": int(user_id),
                    "WhPerMile": 250,
                    "kWhRequested": requested,
                    "milesRequested": int(requested * 4),
                    "minutesAvailable": minutes,
                    "modifiedAt": format_time(connect + timedelta(minutes=2)),
                    "paymentRequired": True,
                    "requestedDeparture": format_time(connect + timedelta(minutes=minutes)),
                }]

            sid = f"{station_id.replace('-', '_')}_{connect.strftime('%Y-%m-%d %H:%M:%S')}"
            sessions.append({
                "_id": hashlib.md5(sid.encode()).hexdigest()[:24],
                "sessionID": sid,
                "siteID": "0002",
                "clusterID": "0039",
                "spaceID": space_id,
                "stationID": station_id,
                "connectionTime": format_time(connect),
                "disconnectTime": format_time(disconnect.replace(microsecond=0)),
                "doneChargingTime": format_time(done.replace(microsecond=0)),
                "kWhDelivered": round(delivered, 3),
                "timezone": TZ,
                "userID": user_id,
                "userInputs": user_inputs,
            })

    if inject_issues and sessions:
        bad = dict(sessions[0], sessionID="bad_disconnect_before_connect")
        bad["disconnectTime"], bad["connectionTime"] = bad["connectionTime"], bad["disconnectTime"]
        missing = dict(sessions[1], sessionID="bad_missing_energy", kWhDelivered=None)
        sessions += [bad, missing, dict(sessions[2])]  # the last one is an exact duplicate
    return sessions


def write_jsonl(sessions: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for s in sessions:
            f.write(json.dumps(s) + "\n")
    return path
