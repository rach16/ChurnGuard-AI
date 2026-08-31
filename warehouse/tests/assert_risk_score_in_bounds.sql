-- A weighted sum of five 0-1 factors cannot leave [0, 100]. If it does, a factor
-- clamp has been lost.
select customer_id, risk_score
from {{ ref('customer_health_score') }}
where risk_score < 0 or risk_score > 100
