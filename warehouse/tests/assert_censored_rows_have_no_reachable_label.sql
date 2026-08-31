-- A customer censored before a horizon cannot carry a label for it. Asserting
-- "did not churn" about a period we never observed is the core error ADR-0008
-- exists to prevent.
select customer_id, week_start, weeks_to_event, churn_within_1q
from {{ ref('train_survival') }}
where event_observed = 0 and weeks_to_event < 13 and churn_within_1q is not null
