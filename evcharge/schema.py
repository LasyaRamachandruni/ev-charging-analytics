"""The ACN-Data session record and how we flatten it.

A raw session from the ACN-Data API looks like this (dates are RFC 1123 strings in GMT):

    {
      "_id": "5c2e8...", "sessionID": "2_39_78_362_2019-01-02 14:57:25.164453",
      "siteID": "0002", "clusterID": "0039", "spaceID": "CA-317", "stationID": "2-39-78-362",
      "connectionTime": "Wed, 02 Jan 2019 14:57:25 GMT",
      "disconnectTime": "Wed, 02 Jan 2019 23:12:03 GMT",
      "doneChargingTime": "Wed, 02 Jan 2019 18:49:51 GMT",
      "kWhDelivered": 9.68, "timezone": "America/Los_Angeles", "userID": "000000561",
      "userInputs": [{"kWhRequested": 25.0, "milesRequested": 100, "WhPerMile": 250,
                      "minutesAvailable": 463, "requestedDeparture": "Thu, 03 Jan 2019 02:39:25 GMT",
                      "modifiedAt": "Wed, 02 Jan 2019 14:58:44 GMT", "paymentRequired": true,
                      "userID": 561}]
    }

`userInputs` is only present when the driver used the mobile app, and a driver can
update their request several times; we keep the latest one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

RAW_REQUIRED = ("sessionID", "stationID", "connectionTime", "disconnectTime", "kWhDelivered", "timezone")

# Flattened columns written to the bronze layer, in order.
COLUMNS = [
    "session_id",
    "site",
    "site_id",
    "cluster_id",
    "station_id",
    "space_id",
    "user_id",
    "timezone",
    "connection_time",
    "disconnect_time",
    "done_charging_time",
    "kwh_delivered",
    "kwh_requested",
    "miles_requested",
    "wh_per_mile",
    "minutes_available",
    "requested_departure",
    "payment_required",
    "user_input_count",
]

RFC1123 = "%a, %d %b %Y %H:%M:%S GMT"


def parse_time(value: Any) -> datetime | None:
    """Parse an ACN-Data timestamp into an aware UTC datetime (None stays None)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.strptime(value, RFC1123).replace(tzinfo=timezone.utc)


def format_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(RFC1123)


def _latest_user_input(inputs: Any) -> dict | None:
    if not inputs:
        return None
    return max(inputs, key=lambda u: parse_time(u.get("modifiedAt")) or datetime.min.replace(tzinfo=timezone.utc))


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def flatten(raw: dict, site: str) -> dict:
    """Turn one raw ACN-Data session into a flat row. Raises KeyError/ValueError on bad records."""
    missing = [k for k in RAW_REQUIRED if raw.get(k) in (None, "")]
    if missing:
        raise KeyError(f"missing required field(s): {', '.join(missing)}")

    latest = _latest_user_input(raw.get("userInputs"))
    user_id = raw.get("userID")
    return {
        "session_id": str(raw["sessionID"]),
        "site": site,
        "site_id": raw.get("siteID"),
        "cluster_id": raw.get("clusterID"),
        "station_id": str(raw["stationID"]),
        "space_id": raw.get("spaceID"),
        "user_id": None if user_id in (None, "") else str(user_id),
        "timezone": raw["timezone"],
        "connection_time": parse_time(raw["connectionTime"]),
        "disconnect_time": parse_time(raw["disconnectTime"]),
        "done_charging_time": parse_time(raw.get("doneChargingTime")),
        "kwh_delivered": float(raw["kWhDelivered"]),
        "kwh_requested": _num(latest.get("kWhRequested")) if latest else None,
        "miles_requested": _num(latest.get("milesRequested")) if latest else None,
        "wh_per_mile": _num(latest.get("WhPerMile")) if latest else None,
        "minutes_available": _num(latest.get("minutesAvailable")) if latest else None,
        "requested_departure": parse_time(latest.get("requestedDeparture")) if latest else None,
        "payment_required": bool(latest.get("paymentRequired")) if latest else None,
        "user_input_count": len(raw.get("userInputs") or []),
    }


def problems(row: dict) -> list[str]:
    """Row-level validity rules. Rows with problems are quarantined, not loaded."""
    issues = []
    if row["disconnect_time"] <= row["connection_time"]:
        issues.append("disconnect_before_connect")
    if row["kwh_delivered"] < 0:
        issues.append("negative_energy")
    done = row["done_charging_time"]
    if done is not None and not (row["connection_time"] <= done <= row["disconnect_time"]):
        issues.append("done_charging_outside_session")
    hours = (row["disconnect_time"] - row["connection_time"]).total_seconds() / 3600
    if hours > 0 and row["kwh_delivered"] / hours > 30:
        issues.append("implausible_average_power")  # level-2 stations top out well below this
    if hours > 72:
        issues.append("session_longer_than_72h")
    return issues
