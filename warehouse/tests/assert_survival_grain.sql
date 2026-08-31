-- One training row per customer per week.
select customer_id, week_start, count(*) as n
from {{ ref('train_survival') }}
group by customer_id, week_start
having count(*) > 1
