-- Site power demand in 15-minute intervals.
-- Each session's energy is spread evenly over its charging window
-- [connection, done charging], split across the intervals it overlaps.
-- The ACN time series has the true per-minute rates; this is the standard
-- approximation from session-level data and conserves energy exactly
-- (see tests/assert_load_conserves_energy.sql).
with charging as (
    select
        site, timezone, session_id, kwh_delivered,
        connected_at_utc     as t0,
        charging_done_at_utc as t1
    from {{ ref('stg_sessions') }}
    where charging_done_at_utc > connected_at_utc and kwh_delivered > 0
),

pieces as (
    select
        c.*,
        unnest(range(time_bucket(interval 15 minute, t0), t1, interval 15 minute)) as interval_start_utc
    from charging c
),

weighted as (
    select
        site,
        timezone,
        interval_start_utc,
        kwh_delivered
          * (epoch(least(t1, interval_start_utc + interval 15 minute)) - epoch(greatest(t0, interval_start_utc)))
          / (epoch(t1) - epoch(t0)) as energy_kwh
    from pieces
)

select
    site,
    interval_start_utc,
    interval_start_utc at time zone any_value(timezone) as interval_start_local,
    count(*)                                            as charging_sessions,
    round(sum(energy_kwh), 6)                           as energy_kwh,
    round(sum(energy_kwh) / 0.25, 4)                    as power_kw
from weighted
group by site, interval_start_utc
