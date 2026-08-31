-- Customer risk scoring, in SQL.
--
-- This logic previously lived only inside src/core/health_scoring.py, which meant
-- the number driving retention decisions could not be inspected, joined to, or
-- recomputed by anyone without running the API. Here it is queryable, and
-- tests/assert_health_score_matches_python.sql keeps the two implementations honest.
--
-- Weights come from dbt_project.yml vars and mirror RISK_WEIGHTS in the Python.

with as_of as (
    -- Anchor rolling windows to the data, not to wall-clock time.
    select max(week_start) as as_of_date from {{ ref('sl_engagement_weekly') }}
),

latest as (
    select customer_id, engagement_score, feature_adoption_rate
    from {{ ref('sl_engagement_weekly') }}
    where weeks_ago = 1
),

trend as (
    -- Slope of engagement over the recent window. regr_slope is invariant to
    -- shifting x, so -weeks_ago (ascending in time) matches the Python's 0..n-1.
    select
        customer_id,
        case when count(*) >= 3
             then regr_slope(engagement_score, -weeks_ago)
             else 0.0 end as engagement_slope
    from {{ ref('sl_engagement_weekly') }}
    where weeks_ago <= {{ var('trend_window_weeks') }}
    group by customer_id
),

tickets as (
    select
        c.customer_id,
        count(t.ticket_id) filter (
            where t.created_date >= (select as_of_date from as_of)
                                    - interval '{{ var("ticket_window_days") }}' day
        ) as tickets_30d,
        avg(t.csat_score) as mean_csat
    from {{ ref('sl_customers') }} c
    left join {{ ref('sl_support_tickets') }} t using (customer_id)
    group by c.customer_id
),

last_contact as (
    select customer_id, max(interaction_date) as last_interaction_date
    from {{ ref('sl_interactions') }}
    group by customer_id
),

factors as (
    select
        c.customer_id,
        l.engagement_score,
        l.feature_adoption_rate,
        tr.engagement_slope,
        tk.tickets_30d,
        tk.mean_csat,
        date_diff('day', lc.last_interaction_date, (select as_of_date from as_of))
            as last_engagement_days,

        -- Each signal mapped onto a 0-1 adverse fraction (1 = worst).
        greatest(0.0, least(1.0, 1.0 - l.engagement_score))            as f_engagement,
        greatest(0.0, least(1.0, 1.0 - l.feature_adoption_rate))       as f_adoption,
        greatest(0.0, least(1.0, -tr.engagement_slope / 0.01))         as f_trend,
        greatest(0.0, least(1.0, tk.tickets_30d / {{ var('high_ticket_volume') }}.0)) as f_support,
        -- No tickets is not evidence of unhappiness, so treat it as neutral.
        coalesce(greatest(0.0, least(1.0, (5.0 - tk.mean_csat) / 4.0)), 0.5) as f_satisfaction
    from {{ ref('sl_customers') }} c
    join latest l using (customer_id)
    join trend tr using (customer_id)
    join tickets tk using (customer_id)
    left join last_contact lc using (customer_id)
),

scored as (
    select
        *,
        round(100 * (
              {{ var('weight_engagement') }}   * f_engagement
            + {{ var('weight_adoption') }}     * f_adoption
            + {{ var('weight_trend') }}        * f_trend
            + {{ var('weight_support') }}      * f_support
            + {{ var('weight_satisfaction') }} * f_satisfaction
        ), 1) as risk_score
    from factors
)

select
    s.customer_id,
    d.customer_num,
    d.company_name,
    d.segment,
    d.arr,
    d.tenure_years,
    d.is_churned,
    s.risk_score,
    case when s.risk_score >= 80 then 'Critical'
         when s.risk_score >= 60 then 'High'
         when s.risk_score >= 40 then 'Medium'
         else 'Low' end as risk_level,
    -- The dominant weighted factor names the reason, so label always matches maths.
    case greatest(
            {{ var('weight_engagement') }}   * s.f_engagement,
            {{ var('weight_adoption') }}     * s.f_adoption,
            {{ var('weight_trend') }}        * s.f_trend,
            {{ var('weight_support') }}      * s.f_support,
            {{ var('weight_satisfaction') }} * s.f_satisfaction)
        when {{ var('weight_engagement') }}   * s.f_engagement   then 'Low engagement'
        when {{ var('weight_adoption') }}     * s.f_adoption     then 'Feature gaps'
        when {{ var('weight_trend') }}        * s.f_trend        then 'Declining usage'
        when {{ var('weight_support') }}      * s.f_support      then 'Support issues'
        else 'Poor onboarding' end as risk_reason,
    case when s.engagement_slope < -0.002 then 'increasing'
         when s.engagement_slope >  0.002 then 'decreasing'
         else 'stable' end as trend,
    round(s.engagement_score, 3)      as engagement_score,
    round(s.feature_adoption_rate, 2) as feature_adoption_rate,
    s.tickets_30d                     as support_tickets_30d,
    round(s.mean_csat, 2)             as mean_csat,
    s.last_engagement_days,
    s.f_engagement, s.f_adoption, s.f_trend, s.f_support, s.f_satisfaction
from scored s
join {{ ref('dim_customer') }} d using (customer_id)
