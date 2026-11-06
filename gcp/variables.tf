variable "project_id" {
  type        = string
  description = "The Google Cloud project (gcp/bootstrap must have been applied to it)."
}

variable "project_name" {
  type        = string
  default     = "meetings"
  description = "Name prefix for resources, and the project_name label."
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "db_tier" {
  type        = string
  default     = "db-f1-micro"
  description = "Cloud SQL machine type: the smallest shared-core one by default."
}

variable "cognito_region" {
  type    = string
  default = "us-east-1"
}

variable "cognito_user_pool_id" {
  type        = string
  description = "The user pool in infra/auth.tf (sign-in stays on AWS): `terraform output` in infra/."
}

variable "cognito_client_id" {
  type = string
}

variable "app_origins" {
  type        = list(string)
  description = "Origins allowed by the API's CORS. The site itself calls /api on its own origin (Firebase rewrite)."
}
