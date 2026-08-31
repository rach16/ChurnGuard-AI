-- Weekly engagement fact, grain: customer x week.
select
    e.customer_id,
    e.week_start,
    e.weeks_ago,
    e.engagement_score,
    e.feature_adoption_rate,
    e.active_users,
    e.sessions,
    c.segment,
    c.arr
from {{ ref('sl_engagement_weekly') }} e
join {{ ref('sl_customers') }} c using (customer_id)
