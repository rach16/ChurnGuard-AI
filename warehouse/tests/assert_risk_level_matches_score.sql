-- The band label must follow from the score, not drift from it.
select customer_id, risk_score, risk_level
from {{ ref('customer_health_score') }}
where risk_level != case
        when risk_score >= 80 then 'Critical'
        when risk_score >= 60 then 'High'
        when risk_score >= 40 then 'Medium'
        else 'Low' end
