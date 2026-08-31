-- Grain check: one engagement row per customer per week.
select customer_id, week_start, count(*) as n
from {{ ref('fct_engagement_weekly') }}
group by customer_id, week_start
having count(*) > 1
