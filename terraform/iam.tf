data "aws_caller_identity" "current" {}

resource "aws_iam_role" "cost_detective_lambda" {
  name = "cost-detective-reactive-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Least-privilege: exactly what the Lambda needs to fetch cost data, read its
# two secrets, and write logs. No wildcards, no admin-adjacent permissions -
# same discipline as cost-detective-ci (ce:GetCostAndUsage only) elsewhere
# in this project.
resource "aws_iam_role_policy" "cost_detective_lambda_policy" {
  name = "cost-detective-reactive-policy"
  role = aws_iam_role.cost_detective_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CostExplorerReadOnly"
        Effect   = "Allow"
        Action   = ["ce:GetCostAndUsage"]
        Resource = "*"
      },
      {
        Sid    = "ReadSecretsFromSSM"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.slack_webhook_ssm_param}",
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.anthropic_api_key_ssm_param}",
        ]
      },
      {
        Sid    = "LambdaLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/cost-detective-reactive*"
      }
    ]
  })
}
