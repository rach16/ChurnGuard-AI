# A billing alarm is the cheapest insurance in this stack. NAT gateways, idle
# Fargate tasks and forgotten load balancers all bill by the hour, and the default
# way to discover that is a monthly invoice.

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email # requires confirming a link in the inbox
}

# AWS publishes EstimatedCharges to us-east-1 only, regardless of where the
# resources live.
provider "aws" {
  alias  = "billing"
  region = "us-east-1"
}

resource "aws_cloudwatch_metric_alarm" "monthly_spend" {
  provider = aws.billing

  alarm_name          = "${local.name}-monthly-spend"
  alarm_description   = "Estimated charges above $${var.monthly_budget_usd}"
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  dimensions          = { Currency = "USD" }
  statistic           = "Maximum"
  period              = 21600 # 6h; the metric only updates a few times a day
  evaluation_periods  = 1
  threshold           = var.monthly_budget_usd
  comparison_operator = "GreaterThanThreshold"

  alarm_actions = [aws_sns_topic.alerts.arn]
  # Missing data is normal early in a billing cycle and must not read as an alarm.
  treat_missing_data = "notBreaching"
}
