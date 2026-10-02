-- Share of the observation window each station was occupied (car plugged in)
-- and actually delivering energy. Idle-but-occupied time is capacity lost to
-- cars that finished charging but did not leave.
with window_per_site as (
    select site, date_diff('day', min(local_date), max(local_date)) + 1 as days
    from {{ ref('stg_sessions') }}
    group by site
)

select
    s.station_id,
    s.site,
    count(*)                                                    as sessions,
    round(sum(s.connected_hours) / (w.days * 24), 4)            as occupied_share,
    round(sum(s.charging_hours) / (w.days * 24), 4)             as charging_share,
    round(sum(s.idle_hours) / nullif(sum(s.connected_hours), 0), 4) as idle_share_of_occupied
from {{ ref('stg_sessions') }} s
join window_per_site w using (site)
group by s.station_id, s.site, w.days
