select
    station_id,
    any_value(site)                         as site,
    any_value(space_id)                     as space_id,
    min(local_date)                         as first_session_date,
    max(local_date)                         as last_session_date,
    count(*)                                as sessions,
    round(sum(kwh_delivered), 3)            as total_kwh
from {{ ref('stg_sessions') }}
group by station_id
