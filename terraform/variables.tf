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

variable "workload_account_id" {
  description = "AWS account ID of the workload account the cross-account scan reads from (from the multi-account-observability project)."
  type        = string
  default     = "772378124091"
}

variable "cross_account_role_name" {
  description = "IAM role name in the workload account that this Lambda assumes for the orphaned-resource scan (created by multi-account-observability)."
  type        = string
  default     = "cross-account-monitoring-reader"
}

variable "cross_account_external_id_ssm_param" {
  description = "SSM Parameter Store path holding the external ID required to assume the cross-account role (SecureString) - same value set in multi-account-observability's terraform.tfvars."
  type        = string
  default     = "/cost-detective/cross-account-external-id"
}

variable "orphaned_scan_schedule_expression" {
  description = "EventBridge schedule expression for the orphaned-resource scan - daily by default, since this checks steady-state waste, not fast-moving anomalies."
  type        = string
  default     = "rate(1 day)"
}
