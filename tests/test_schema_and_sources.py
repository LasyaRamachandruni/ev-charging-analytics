from datetime import date, datetime, timezone

import pytest

from evcharge.schema import flatten, parse_time, problems
from evcharge.sources.acn import ACNClient
from evcharge.sources.synthetic import generate

RAW = {
    "_id": "abc",
    "sessionID": "2_39_78_362_2019-01-02 14:57:25",
    "siteID": "0002",
    "clusterID": "0039",
    "spaceID": "CA-317",
    "stationID": "2-39-78-362",
    "connectionTime": "Wed, 02 Jan 2019 14:57:25 GMT",
    "disconnectTime": "Wed, 02 Jan 2019 23:12:03 GMT",
    "doneChargingTime": "Wed, 02 Jan 2019 18:49:51 GMT",
    "kWhDelivered": 9.68,
    "timezone": "America/Los_Angeles",
    "userID": "000000561",
    "userInputs": [
        {"kWhRequested": 20.0, "modifiedAt": "Wed, 02 Jan 2019 14:58:00 GMT", "minutesAvailable": 400},
        {"kWhRequested": 25.0, "modifiedAt": "Wed, 02 Jan 2019 15:30:00 GMT", "minutesAvailable": 463,
         "requestedDeparture": "Thu, 03 Jan 2019 02:39:25 GMT", "paymentRequired": True},
    ],
}


def test_parse_time_is_utc():
    assert parse_time("Wed, 02 Jan 2019 14:57:25 GMT") == datetime(2019, 1, 2, 14, 57, 25, tzinfo=timezone.utc)
    assert parse_time(None) is None


def test_flatten_keeps_latest_user_input():
    row = flatten(RAW, "caltech")
    assert row["kwh_requested"] == 25.0  # the later of two updates
    assert row["minutes_available"] == 463
    assert row["user_input_count"] == 2
    assert row["payment_required"] is True
    assert row["site"] == "caltech" and row["station_id"] == "2-39-78-362"


def test_flatten_without_user_inputs():
    row = flatten(dict(RAW, userInputs=None, userID=None), "caltech")
    assert row["kwh_requested"] is None and row["user_id"] is None and row["user_input_count"] == 0


def test_flatten_rejects_missing_fields():
    with pytest.raises(KeyError):
        flatten(dict(RAW, kWhDelivered=None), "caltech")


@pytest.mark.parametrize(
    "change, issue",
    [
        ({"disconnectTime": "Wed, 02 Jan 2019 14:00:00 GMT"}, "disconnect_before_connect"),
        ({"kWhDelivered": -1}, "negative_energy"),
        ({"doneChargingTime": "Thu, 03 Jan 2019 01:00:00 GMT"}, "done_charging_outside_session"),
        ({"kWhDelivered": 400}, "implausible_average_power"),
        ({"disconnectTime": "Mon, 07 Jan 2019 14:00:00 GMT", "doneChargingTime": None}, "session_longer_than_72h"),
    ],
)
def test_problems_flags_bad_rows(change, issue):
    assert issue in problems(flatten(dict(RAW, **change), "caltech"))


def test_valid_row_has_no_problems():
    assert problems(flatten(RAW, "caltech")) == []


# --- API client --------------------------------------------------------------

class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, auth=None, timeout=None):
        self.calls.append((url, params, auth))
        return self.responses.pop(0)


def test_client_follows_pagination_and_retries(monkeypatch):
    monkeypatch.setattr("evcharge.sources.acn.time.sleep", lambda s: None)
    http = FakeHTTP([
        FakeResponse(503, {}),  # transient error, retried
        FakeResponse(200, {"_items": [{"sessionID": "a"}, {"sessionID": "b"}],
                           "_links": {"next": {"href": "sessions/caltech?page=2"}}}),
        FakeResponse(200, {"_items": [{"sessionID": "c"}], "_links": {}}),
    ])
    client = ACNClient(token="tok", url="https://api.test/", session=http)
    ids = [s["sessionID"] for s in client.sessions("caltech", datetime(2019, 1, 1, tzinfo=timezone.utc),
                                                   datetime(2019, 2, 1, tzinfo=timezone.utc))]
    assert ids == ["a", "b", "c"]
    first_url, first_params, auth = http.calls[0]
    assert first_url == "https://api.test/sessions/caltech" and auth == ("tok", "")
    assert 'connectionTime>="Tue, 01 Jan 2019 00:00:00 GMT"' in first_params["where"]
    assert http.calls[2] == ("https://api.test/sessions/caltech?page=2", None, ("tok", ""))


def test_client_needs_token(monkeypatch):
    monkeypatch.delenv("ACN_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        ACNClient()


def test_client_rejects_unknown_site():
    client = ACNClient(token="t", session=FakeHTTP([]))
    with pytest.raises(ValueError):
        list(client.sessions("nowhere", datetime(2019, 1, 1, tzinfo=timezone.utc), datetime(2019, 1, 2, tzinfo=timezone.utc)))


# --- synthetic data ------------------------------------------------------------

def test_synthetic_is_deterministic_and_valid():
    a = generate(date(2019, 1, 7), 14, seed=3)
    assert a == generate(date(2019, 1, 7), 14, seed=3)
    rows = [flatten(r, "caltech") for r in a]
    assert all(problems(r) == [] for r in rows)
    assert len({r["session_id"] for r in rows}) == len(rows)


def test_synthetic_weekdays_busier_than_weekends():
    rows = [flatten(r, "caltech") for r in generate(date(2019, 1, 7), 28)]
    by_day = {}
    for r in rows:
        d = r["connection_time"].date()
        by_day[d] = by_day.get(d, 0) + 1
    weekday = [n for d, n in by_day.items() if d.weekday() < 5]
    weekend = [n for d, n in by_day.items() if d.weekday() >= 5]
    assert sum(weekday) / len(weekday) > 2 * sum(weekend) / len(weekend)
