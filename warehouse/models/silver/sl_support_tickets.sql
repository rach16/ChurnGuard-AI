select
    ticket_id,
    customer_id,
    category,
    issue_type,
    severity,
    cast(created_date as date)      as created_date,
    cast(resolution_hours as integer) as resolution_hours,
    cast(csat_score as integer)     as csat_score
from {{ ref('br_support_tickets') }}
