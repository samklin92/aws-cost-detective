resource "aws_sns_topic" "budget_alerts" {
  name = "cost-detective-budget-alerts"
}

# AWS Budgets publishes to SNS via the budgets.amazonaws.com service principal -
# this policy grants only that specific service, not a wildcard principal.
resource "aws_sns_topic_policy" "allow_budgets_publish" {
  arn = aws_sns_topic.budget_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowBudgetsPublish"
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.budget_alerts.arn
    }]
  })
}

resource "aws_lambda_function" "cost_detective_reactive" {
  function_name = "cost-detective-reactive"
  role          = aws_iam_role.cost_detective_lambda.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"
  timeout       = 30 # Cost Explorer call + Claude triage call comfortably fits; default 3s does not
  memory_size   = 256

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  environment {
    variables = {
      SLACK_WEBHOOK_SSM_PARAM     = var.slack_webhook_ssm_param
      ANTHROPIC_API_KEY_SSM_PARAM = var.anthropic_api_key_ssm_param
    }
  }
}

resource "aws_lambda_permission" "allow_sns_invoke" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_detective_reactive.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.budget_alerts.arn
}

resource "aws_sns_topic_subscription" "lambda_subscription" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.cost_detective_reactive.arn
}

# Two notification thresholds: 80% of ACTUAL spend (something has already
# happened) and 100% of FORECASTED spend (something is about to happen at
# current trajectory) - catches both a real overspend and an early warning
# before the month closes.
resource "aws_budgets_budget" "monthly_cost_detective" {
  name         = "cost-detective-monthly-budget"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_limit
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
    subscriber_email_addresses = var.budget_notification_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
    subscriber_email_addresses = var.budget_notification_emails
  }
}

output "sns_topic_arn" {
  value = aws_sns_topic.budget_alerts.arn
}

output "lambda_function_name" {
  value = aws_lambda_function.cost_detective_reactive.function_name
}
