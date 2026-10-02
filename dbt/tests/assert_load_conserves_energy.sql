-- The 15-minute load curve must account for every kWh delivered (within rounding).
with delivered as (
    select site, sum(kwh_delivered) as kwh
    from {{ ref('stg_sessions') }}
    where charging_done_at_utc > connected_at_utc and kwh_delivered > 0
    group by site
),
spread as (
    select site, sum(energy_kwh) as kwh from {{ ref('fct_site_load_15min') }} group by site
)
select d.site, d.kwh as delivered_kwh, s.kwh as load_kwh
from delivered d left join spread s using (site)
where s.kwh is null or abs(d.kwh - s.kwh) > 0.001 * d.kwh + 0.01
