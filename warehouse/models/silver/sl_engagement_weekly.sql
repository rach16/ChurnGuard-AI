-- One row per customer per week. week_index counts backwards from the customer's
-- most recent observation, so "last 12 weeks" is expressible without date maths.
with typed as (
    select
        customer_id,
        cast(week_start as date)               as week_start,
        cast(engagement_score as double)       as engagement_score,
        cast(feature_adoption_rate as double)  as feature_adoption_rate,
        cast(active_users as integer)          as active_users,
        cast(sessions as integer)              as sessions
    from {{ ref('br_engagement_snapshots') }}
)
select
    *,
    row_number() over (partition by customer_id order by week_start desc) as weeks_ago
from typed
