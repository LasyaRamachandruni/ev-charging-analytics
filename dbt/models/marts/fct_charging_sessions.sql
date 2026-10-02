select
    *,
    case when has_request
         then request_fulfillment >= {{ var('request_met_threshold') }} end as request_met,
    idle_hours > {{ var('overstay_hours') }}                                 as overstayed
from {{ ref('stg_sessions') }}
