-- Daily operating metrics per site.
select
    site,
    local_date,
    any_value(is_weekend)                                       as is_weekend,
    count(*)                                                    as sessions,
    count(distinct user_id)                                     as identified_users,
    round(sum(kwh_delivered), 3)                                as energy_kwh,
    round(avg(connected_hours), 3)                              as avg_connected_hours,
    round(avg(idle_hours), 3)                                   as avg_idle_hours,
    round(avg(case when request_met then 1.0 when has_request then 0.0 end), 4) as share_requests_met,
    round(avg(case when overstayed then 1.0 else 0.0 end), 4)  as share_overstayed
from {{ ref('fct_charging_sessions') }}
group by site, local_date
