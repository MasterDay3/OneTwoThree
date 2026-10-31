# Was infra/cognito.yaml (Lab 4): a user pool for email + password sign-in, a public app client for the
# SPA, a managed-login domain, and Google as an identity provider when google_client_id is set.

locals {
  google_enabled = var.google_client_id != ""
}

resource "aws_cognito_user_pool" "main" {
  name                     = "${var.project_name}-users"
  user_pool_tier           = "ESSENTIALS"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Changing the username attributes forces a NEW pool: every user would be lost.
  username_configuration {
    case_sensitive = false
  }

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_numbers                  = true
    require_uppercase                = false
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  # Cognito's own sender (about 50 emails a day).
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # Changing the schema forces a NEW pool. `name` (optional) is a standard attribute, so AWS does not
  # list it here and it needs no block.
  schema {
    name                     = "email"
    attribute_data_type      = "String"
    developer_only_attribute = false
    required                 = true
    mutable                  = true

    string_attribute_constraints {
      min_length = "0"
      max_length = "2048"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Hosted UI / OAuth endpoints, needed for Google. The prefix must be unique across AWS.
resource "aws_cognito_user_pool_domain" "main" {
  user_pool_id          = aws_cognito_user_pool.main.id
  domain                = "${var.project_name}-${local.account_id}"
  managed_login_version = 2
}

resource "aws_cognito_identity_provider" "google" {
  count = local.google_enabled ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    client_id        = var.google_client_id
    client_secret    = var.google_client_secret
    authorize_scopes = "openid email profile"
    # Filled in by Cognito for a Google provider; listed so the plan stays empty.
    attributes_url                = "https://people.googleapis.com/v1/people/me?personFields="
    attributes_url_add_attributes = "true"
    authorize_url                 = "https://accounts.google.com/o/oauth2/v2/auth"
    oidc_issuer                   = "https://accounts.google.com"
    token_request_method          = "POST"
    token_url                     = "https://www.googleapis.com/oauth2/v4/token"
  }

  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    name           = "name"
    username       = "sub"
  }
}

# Public client (no secret): the browser talks to Cognito directly; the backend only verifies tokens.
resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.project_name}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  id_token_validity      = 60
  access_token_validity  = 60
  refresh_token_validity = 30

  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }

  # The reference makes the client wait for the Google provider when it exists.
  supported_identity_providers = concat(["COGNITO"], aws_cognito_identity_provider.google[*].provider_name)

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = [for o in var.app_origins : "${o}/auth/callback"]
  logout_urls                          = [for o in var.app_origins : "${o}/"]
}

# Without a style, a version 2 domain has no sign-in page to show.
resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.main.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
}
