-- A row on or after the churn date would expose the event being predicted.
select customer_id, week_start, churn_date, weeks_to_event
from {{ ref('train_survival') }}
where weeks_to_event <= 0
