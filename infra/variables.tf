variable "project_name" {
  type        = string
  default     = "meetings"
  description = "Name prefix for all resources, and the PROJECT_NAME tag."
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "backend_image_uri" {
  type        = string
  description = <<-EOT
    Backend image the report builder and the agenda parser start with. CI moves both functions to each
    new image with `update-function-code`, so Terraform ignores later changes of it (see ai.tf, reports.tf).
  EOT
}

variable "architecture" {
  type    = string
  default = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64 (the platform the image was built for)."
  }
}

variable "log_retention_days" {
  type    = number
  default = 7
}

# --- auth.tf (Cognito) ---

variable "app_origins" {
  type        = list(string)
  description = "Origins of the SPA (local dev servers and the site): Cognito redirect URLs and the parser's CORS."
}

variable "google_client_id" {
  type        = string
  default     = ""
  description = "OAuth client ID from Google Cloud. Empty = Google sign-in is not set up."
}

variable "google_client_secret" {
  type        = string
  default     = ""
  sensitive   = true
  description = "OAuth client secret from Google Cloud. Set it in the HCP Terraform workspace, never in a file."
}

# --- reports.tf (weekly report) ---

variable "db_host" {
  type        = string
  description = "Writer endpoint of the Aurora cluster (`make aws-db`)."
}

variable "db_resource_id" {
  type        = string
  description = "The cluster's resource ID (cluster-...), which IAM database authentication is scoped to."
}

variable "db_username" {
  type    = string
  default = "postgres"
}

variable "db_name" {
  type    = string
  default = "postgres"
}

variable "schedule_expression" {
  type        = string
  default     = "cron(0 7 ? * MON *)"
  description = "When the report is built. rate(5 minutes) to test without waiting for Monday."
}

variable "report_timezone" {
  type    = string
  default = "Europe/Kyiv"
}

variable "report_fail_week" {
  type        = string
  default     = ""
  description = "Lab 5 exercise only: the builder fails for this ISO week, so its message lands in the DLQ."
}

variable "mail_domain" {
  type        = string
  description = "Domain the report is sent from (reports@<domain>)."
}

variable "report_recipients" {
  type        = list(string)
  description = "Who gets the report. The first one is created as an SES identity for the sandbox."

  validation {
    condition     = length(var.report_recipients) > 0
    error_message = "report_recipients needs at least one address."
  }
}

# --- ai.tf (agenda parser) ---

variable "parser_model" {
  type    = string
  default = "anthropic.claude-opus-5-5"
}

variable "budget_email" {
  type        = string
  description = "Who gets the monthly budget alarm."
}

variable "budget_limit_usd" {
  type    = number
  default = 5
}
