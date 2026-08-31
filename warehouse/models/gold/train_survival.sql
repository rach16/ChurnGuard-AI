-- Training table for the survival model: point-in-time features plus labels.
--
-- One row per customer per observed week. Features come from feat_customer_week
-- and use only data up to that week; labels look forward from it.
--
-- Censoring is the thing to get right. 64.5% of customers are still active as of
-- the last observation, so their churn date is unknown -- not absent. Labelling
-- them "will not churn" asserts something the data does not say and is the usual
-- way churn models end up well-scored and useless. event_observed carries that
-- distinction, and any model trained here must respect it. See ADR-0008.

with as_of as (
    select max(week_start) as as_of_date from {{ ref('sl_engagement_weekly') }}
),

labelled as (
    select
        f.*,

        c.is_churned,
        c.churn_date,
        c.churn_category,

        -- Weeks from this observation until the event, or until censoring for a
        -- customer still active at the end of the observation window.
        case
            when c.churn_date is not null
                then date_diff('week', f.week_start, c.churn_date)
            else date_diff('week', f.week_start, (select as_of_date from as_of))
        end as weeks_to_event,

        -- 1 = the event was observed, 0 = right-censored at that horizon.
        case when c.churn_date is not null then 1 else 0 end as event_observed

    from {{ ref('feat_customer_week') }} f
    join {{ ref('sl_customers') }} c using (customer_id)
)

select
    *,

    -- Discrete-time hazard label at a 4-week period: did the event fall in the
    -- period immediately following this observation? This is what a binary
    -- classifier is fitted to; chaining the resulting hazards gives the survival
    -- curve, and its median is the predicted churn date.
    case
        when event_observed = 1 and weeks_to_event > 0 and weeks_to_event <= 4 then 1
        else 0
    end as event_in_next_period,

    -- Horizon labels for display and for sanity-checking the survival curve.
    -- NULL rather than 0 where the horizon extends past the censoring point:
    -- a customer censored in 6 weeks tells us nothing about the next 26.
    case
        when event_observed = 1 and weeks_to_event > 0 and weeks_to_event <= 13 then 1
        when weeks_to_event >= 13 then 0
    end as churn_within_1q,
    case
        when event_observed = 1 and weeks_to_event > 0 and weeks_to_event <= 26 then 1
        when weeks_to_event >= 26 then 0
    end as churn_within_2q,
    case
        when event_observed = 1 and weeks_to_event > 0 and weeks_to_event <= 39 then 1
        when weeks_to_event >= 39 then 0
    end as churn_within_3q,

    -- Backtesting splits by time, never at random: a random split puts the same
    -- customer either side of the boundary and leaks the future into the past.
    cast(year(week_start) as varchar) || '-Q' || cast(quarter(week_start) as varchar)
        as observation_quarter

from labelled
-- An observation on or after the churn date carries no forward information and
-- would let the model see the event it is meant to predict.
where weeks_to_event > 0
