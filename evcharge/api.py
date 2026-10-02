"""REST API for the web app.

    evcharge serve                         # DuckDB warehouse (development)
    EVCHARGE_PG_DSN=postgresql://... evcharge serve   # Postgres (production)

Every response has a pydantic model, so /openapi.json fully describes the API.
The web app's TypeScript types are generated from it (web/src/api/schema.ts),
which means the backend and frontend can't silently disagree about a field.
When web/dist exists, the built web app is served at /.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .serving.store import DuckDBStore, NotFound, PostgresStore, Store

WEB_DIST = Path(os.environ.get("EVCHARGE_WEB_DIST", Path(__file__).resolve().parent.parent / "web" / "dist"))


# -- response models -------------------------------------------------------

class Health(BaseModel):
    status: str
    backend: str


class Site(BaseModel):
    site: str
    timezone: str
    first_date: date
    last_date: date
    sessions: int
    stations: int
    energy_kwh: float


class Summary(BaseModel):
    site: str
    start: date
    end: date
    days: int
    sessions: int
    identified_users: int
    energy_kwh: float
    avg_connected_hours: float | None
    avg_idle_hours: float | None
    share_requests_met: float | None
    share_overstayed: float | None
    peak_kw: float | None
    peak_at: datetime | None  # local time at the site


class DailyRow(BaseModel):
    local_date: date
    is_weekend: bool
    sessions: int
    identified_users: int
    energy_kwh: float
    avg_connected_hours: float
    avg_idle_hours: float
    share_requests_met: float | None
    share_overstayed: float


class LoadSlot(BaseModel):
    slot: int
    time: str
    weekday_avg_kw: float
    weekday_max_kw: float
    weekend_avg_kw: float
    weekend_max_kw: float


class LoadProfile(BaseModel):
    site: str
    start: date
    end: date
    weekdays: int
    weekend_days: int
    slots: list[LoadSlot]


class Station(BaseModel):
    station_id: str
    space_id: str | None
    sessions: int
    total_kwh: float
    occupied_share: float
    charging_share: float
    idle_share_of_occupied: float | None
    first_session_date: date
    last_session_date: date


class Session(BaseModel):
    session_id: str
    station_id: str
    user_id: str | None
    connected_at_utc: datetime
    connected_at_local: datetime
    disconnected_at_utc: datetime
    connected_hours: float
    charging_hours: float
    idle_hours: float
    kwh_delivered: float
    kwh_requested: float | None
    request_met: bool | None
    overstayed: bool


class SessionPage(BaseModel):
    site: str
    start: date
    end: date
    items: list[Session]
    next_cursor: str | None


# -- app -------------------------------------------------------------------

def store_from_env() -> Store:
    dsn = os.environ.get("EVCHARGE_PG_DSN")
    if dsn:
        return PostgresStore(dsn)
    return DuckDBStore(os.environ.get("EVCHARGE_WAREHOUSE", "data/warehouse.duckdb"))


def _clean(x):
    """Engine values -> JSON-friendly: Decimal (Postgres avg) -> float, aware timestamps -> UTC."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, datetime) and x.tzinfo is not None:
        return x.astimezone(timezone.utc)
    return x


def create_app(store: Store | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store or store_from_env()
        yield
        app.state.store.close()

    app = FastAPI(title="EV Charging Analytics API", version="1.0", lifespan=lifespan)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_request(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def st(request: Request) -> Store:
        return request.app.state.store

    @app.get("/api/health", response_model=Health)
    def health(request: Request):
        try:
            st(request).ping()
        except Exception as exc:  # report, so an orchestrator sees why
            raise HTTPException(503, f"database unavailable: {exc}") from exc
        return {"status": "ok", "backend": st(request).dialect}

    @app.get("/api/sites", response_model=list[Site])
    def sites(request: Request):
        return _clean(st(request).sites())

    @app.get("/api/sites/{site}/summary", response_model=Summary)
    def summary(request: Request, site: str, start: date | None = None, end: date | None = None):
        return _clean(st(request).summary(site, start, end))

    @app.get("/api/sites/{site}/daily", response_model=list[DailyRow])
    def daily(request: Request, site: str, start: date | None = None, end: date | None = None):
        return _clean(st(request).daily(site, start, end))

    @app.get("/api/sites/{site}/load-profile", response_model=LoadProfile)
    def load_profile(request: Request, site: str, start: date | None = None, end: date | None = None):
        return _clean(st(request).load_profile(site, start, end))

    @app.get("/api/sites/{site}/stations", response_model=list[Station])
    def stations(request: Request, site: str):
        return _clean(st(request).stations(site))

    @app.get("/api/sites/{site}/sessions", response_model=SessionPage)
    def sessions(request: Request, site: str, start: date | None = None, end: date | None = None,
                 station: str | None = None, limit: int = Query(50, ge=1, le=500), cursor: str | None = None):
        return _clean(st(request).sessions(site, start, end, station, limit, cursor))

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
