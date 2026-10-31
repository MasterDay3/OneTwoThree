# Everything the Makefile and the frontend build read (`terraform output -raw <name>`).

# --- auth ---
output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_issuer" {
  description = "Issuer (iss) of the pool's tokens; its JWKS is at <issuer>/.well-known/jwks.json."
  value       = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

output "cognito_hosted_ui_domain" {
  value = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "cognito_google_enabled" {
  value = tostring(local.google_enabled)
}

# --- reports ---
output "reports_bucket" {
  value = aws_s3_bucket.reports.bucket
}

output "reports_queue_url" {
  description = "Where `make report-now` sends its message."
  value       = aws_sqs_queue.requests.url
}

output "reports_dlq_url" {
  value = aws_sqs_queue.requests_dlq.url
}

output "report_sender" {
  value = "reports@${var.mail_domain}"
}

output "ses_dkim_records" {
  description = "CNAMEs to add at the DNS provider: <token>._domainkey.<domain> -> <token>.dkim.amazonses.com"
  value = [for t in aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens :
  "${t}._domainkey.${var.mail_domain} CNAME ${t}.dkim.amazonses.com"]
}

# --- ai ---
output "parser_url" {
  description = "The frontend's VITE_PARSER_URL."
  value       = aws_lambda_function_url.parser.function_url
}

output "parser_log_group" {
  value = aws_cloudwatch_log_group.parser.name
}
