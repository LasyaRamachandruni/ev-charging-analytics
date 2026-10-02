"""Charts and a metrics summary from the dbt marts.

Writes to an output folder (default: results/):
  load_profile.png        average site power by time of day, weekdays vs weekends
  station_utilization.png how much of each station's time is spent charging vs occupied but idle
  request_outcomes.png    share of app requests fully met, by how long the car stayed
  summary.md              headline numbers
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"


def _style(ax, title, subtitle, xlabel, ylabel):
    ax.figure.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color=INK, pad=22)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color=INK_2, va="bottom")
    ax.set_xlabel(xlabel, color=INK_2, fontsize=10)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=10)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)


def load_profile(con, out: Path, label: str) -> Path:
    df = con.sql("""
        with daily as (
            select site, cast(interval_start_local as date) d, isodow(interval_start_local) >= 6 weekend,
                   hour(interval_start_local) + minute(interval_start_local) / 60.0 tod, power_kw
            from fct_site_load_15min
        ),
        days as (select weekend, count(distinct d) n from daily group by weekend)
        -- average over all days, counting intervals with no charging as 0 kW
        select weekend, tod, sum(power_kw) / any_value(n) avg_kw
        from daily join days using (weekend) group by weekend, tod order by weekend, tod
    """).df()
    fig, ax = plt.subplots(figsize=(8, 4.3))
    for weekend, color, name in ((False, BLUE, "Weekdays"), (True, ORANGE, "Weekends")):
        part = df[df.weekend == weekend]
        if part.empty:
            continue
        ax.plot(part.tod, part.avg_kw, color=color, linewidth=2, label=name)
        peak = part.loc[part.avg_kw.idxmax()]
        ax.annotate(f"{name} peak {peak.avg_kw:.0f} kW at {int(peak.tod):02d}:{int(peak.tod % 1 * 60):02d}",
                    (peak.tod, peak.avg_kw), xytext=(8, 4), textcoords="offset points", fontsize=9, color=INK)
    _style(ax, "Average site power by time of day", label, "Local time of day (hour)", "Average power (kW)")
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    fig.tight_layout()
    path = out / "load_profile.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def station_utilization(con, out: Path, label: str) -> Path:
    df = con.sql("select station_id, charging_share, occupied_share from agg_station_utilization order by occupied_share desc").df()
    fig, ax = plt.subplots(figsize=(8, 4.3))
    x = range(len(df))
    ax.bar(x, df.charging_share * 100, color=BLUE, width=0.8, label="Charging")
    ax.bar(x, (df.occupied_share - df.charging_share) * 100, bottom=df.charging_share * 100,
           color=AQUA, width=0.8, label="Plugged in, finished charging")
    _style(ax, "How each station's time is used", f"{label}; stations sorted by time occupied",
           f"Stations ({len(df)})", "Share of all hours (%)")
    ax.set_xticks([])
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    fig.tight_layout()
    path = out / "station_utilization.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def request_outcomes(con, out: Path, label: str) -> Path:
    df = con.sql("""
        select case when connected_hours < 2 then '< 2 h' when connected_hours < 4 then '2-4 h'
                    when connected_hours < 8 then '4-8 h' else '8 h +' end bucket,
               min(connected_hours) o, avg(case when request_met then 1.0 else 0.0 end) met, count(*) n
        from fct_charging_sessions where has_request group by bucket order by o
    """).df()
    fig, ax = plt.subplots(figsize=(8, 4.3))
    bars = ax.bar(df.bucket, df.met * 100, color=BLUE, width=0.6)
    for b, v, n in zip(bars, df.met, df.n):
        ax.annotate(f"{v:.0%}  (n={n})", (b.get_x() + b.get_width() / 2, v * 100), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=9, color=INK)
    _style(ax, "Share of drivers' energy requests fully met", f"{label}; by how long the car stayed plugged in",
           "Time plugged in", "Requests met (%)")
    ax.set_ylim(0, 110)
    fig.tight_layout()
    path = out / "request_outcomes.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def summary(con, out: Path, label: str) -> Path:
    s = con.sql("""
        select count(*) sessions, count(distinct station_id) stations, count(distinct user_id) users,
               min(local_date) first_day, max(local_date) last_day, sum(kwh_delivered) kwh,
               avg(kwh_delivered) avg_kwh, median(connected_hours) med_connected, median(charging_hours) med_charging,
               avg(case when request_met then 1.0 when has_request then 0.0 end) met,
               avg(case when overstayed then 1.0 else 0.0 end) overstayed
        from fct_charging_sessions
    """).fetchone()
    peak = con.sql("select max(power_kw), arg_max(interval_start_local, power_kw) from fct_site_load_15min").fetchone()
    util = con.sql("select avg(occupied_share), avg(charging_share) from agg_station_utilization").fetchone()
    lines = [
        "# Charging summary",
        "",
        f"_{label}_",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Period | {s[3]} to {s[4]} |",
        f"| Sessions | {s[0]:,} at {s[1]} stations, {s[2]:,} identified drivers |",
        f"| Energy delivered | {s[5]:,.0f} kWh ({s[6]:.1f} kWh per session) |",
        f"| Median time plugged in vs charging | {s[7]:.1f} h vs {s[8]:.1f} h |",
        f"| Station time occupied vs charging | {util[0]:.0%} vs {util[1]:.0%} |",
        f"| App requests fully met | {s[9]:.0%} |",
        f"| Sessions idle > 2 h after charging | {s[10]:.0%} |",
        f"| Highest 15-min site demand | {peak[0]:.0f} kW ({peak[1]:%Y-%m-%d %H:%M}) |",
        "",
    ]
    path = out / "summary.md"
    path.write_text("\n".join(lines))
    return path


def make_report(warehouse: str | Path, out_dir: str | Path = "results", label: str = "") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # same settings as dbt's connection, which can still be open when run in-process by the CLI
    con = duckdb.connect(str(warehouse))
    try:
        return [f(con, out, label) for f in (load_profile, station_utilization, request_outcomes, summary)]
    finally:
        con.close()
