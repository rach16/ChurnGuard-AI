-- Support ticket fact, grain: one row per ticket.
select
    t.ticket_id,
    t.customer_id,
    t.created_date,
    t.category,
    t.issue_type,
    t.severity,
    t.resolution_hours,
    t.csat_score,
    c.segment,
    c.arr
from {{ ref('sl_support_tickets') }} t
join {{ ref('sl_customers') }} c using (customer_id)
