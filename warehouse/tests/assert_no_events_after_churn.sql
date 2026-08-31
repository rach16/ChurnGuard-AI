-- A churned customer cannot raise tickets after the date they left.
select t.ticket_id, t.customer_id, t.created_date, d.churn_date
from {{ ref('fct_support_tickets') }} t
join {{ ref('dim_customer') }} d using (customer_id)
where d.churn_date is not null and t.created_date > d.churn_date
