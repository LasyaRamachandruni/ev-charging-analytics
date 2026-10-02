-- One row per session with durations, local times and request fields derived.
with src as (
    select * from {{ source('bronze', 'sessions') }}
),

derived as (
    select
        session_id,
        site,
        station_id,
        space_id,
        user_id,
        timezone,
        connection_time                                   as connected_at_utc,
        disconnect_time                                   as disconnected_at_utc,
        coalesce(done_charging_time, disconnect_time)     as charging_done_at_utc,
        connection_time at time zone timezone             as connected_at_local,
        kwh_delivered,
        kwh_requested,
        minutes_available,
        date_diff('second', connection_time, disconnect_time) / 3600.0                                   as connected_hours,
        date_diff('second', connection_time, coalesce(done_charging_time, disconnect_time)) / 3600.0     as charging_hours
    from src
)

select
    *,
    cast(connected_at_local as date)                       as local_date,
    hour(connected_at_local)                               as arrival_hour,
    isodow(connected_at_local)                             as iso_weekday,
    isodow(connected_at_local) >= 6                        as is_weekend,
    connected_hours - charging_hours                       as idle_hours,
    kwh_delivered / nullif(charging_hours, 0)              as avg_charging_kw,
    kwh_requested is not null                              as has_request,
    case when kwh_requested > 0
         then least(kwh_delivered / kwh_requested, 1.0) end as request_fulfillment
from derived
