# Extends the existing Lambda (cost_detective_reactive) with permission to
# assume the cross-account role from multi-account-observability, plus an
# EventBridge schedule that triggers the scan on a cadence - independent of
# the existing SNS/Budgets-triggered reactive path in main.tf.

resource "aws_iam_role_policy" "cross_account_assume_policy" {
  name = "cost-detective-cross-account-assume"
  role = aws_iam_role.cost_detective_lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeWorkloadRole"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = "arn:aws:iam::${var.workload_account_id}:role/${var.cross_account_role_name}"
      },
      {
        Sid      = "ReadCrossAccountExternalId"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.cross_account_external_id_ssm_param}"
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "orphaned_scan_schedule" {
  name                = "cost-detective-orphaned-scan-schedule"
  description         = "Triggers the orphaned-resource scan on a schedule - catches steady-state waste that trend-based anomaly detection never flags."
  schedule_expression = var.orphaned_scan_schedule_expression
}

resource "aws_cloudwatch_event_target" "orphaned_scan_target" {
  rule      = aws_cloudwatch_event_rule.orphaned_scan_schedule.name
  target_id = "cost-detective-lambda"
  arn       = aws_lambda_function.cost_detective_reactive.arn
}

resource "aws_lambda_permission" "allow_eventbridge_invoke" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_detective_reactive.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.orphaned_scan_schedule.arn
}
