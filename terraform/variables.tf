variable "monthly_budget_limit" {
  description = "Monthly budget threshold in USD. Set to 50 for testing - adjust to your real account's expected spend before relying on this in production."
  type        = string
  default     = "50"
}

variable "budget_notification_emails" {
  description = "Fallback email(s) for AWS Budgets notifications, in addition to the SNS/Lambda/Slack path. AWS Budgets requires at least one subscriber type - this covers you if the Lambda path ever fails silently."
  type        = list(string)
  default     = []
}

variable "aws_region" {
  description = "Region to deploy the Lambda function in. Cost Explorer data itself is global/us-east-1 regardless of this setting."
  type        = string
  default     = "us-east-1"
}

variable "slack_webhook_ssm_param" {
  description = "SSM Parameter Store path holding the Slack webhook URL (SecureString). Reuses the same webhook the Terraform Drift Detector posts to."
  type        = string
  default     = "/cost-detective/slack-webhook-url"
}

variable "anthropic_api_key_ssm_param" {
  description = "SSM Parameter Store path holding the Anthropic API key (SecureString)."
  type        = string
  default     = "/cost-detective/anthropic-api-key"
}

variable "lambda_zip_path" {
  description = "Path to the packaged Lambda deployment zip (handler.py + cost_engine/ + dependencies). Built via package_lambda.sh."
  type        = string
  default     = "../lambda/build/cost_detective_reactive.zip"
}
