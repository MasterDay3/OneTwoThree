# Was infra/reports.yaml (Lab 5). EventBridge Scheduler (Monday 07:00 Kyiv) and `make report-now` send a
# message to an SQS queue with a dead-letter queue; report-builder (the backend image, another handler)
# reads Aurora and saves reports/<week>.csv to S3; the ObjectCreated event starts report-mailer, which
# emails the file through SES. Neither function runs in a VPC: the express-configuration Aurora has none.
#
# The bucket, the mailer's invoke permission and the bucket notification are three resources here, so
# the CloudFormation cycle (and naming the bucket by hand to break it) is gone.

locals {
  reports_bucket = "${var.project_name}-reports-${local.account_id}"
  lambda_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  lambda_basic_execution = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- Storage -----------------------------------------------------------------------------------------

resource "aws_s3_bucket" "reports" {
  bucket = local.reports_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "delete-old-reports"
    status = "Enabled"

    filter {
      prefix = "reports/"
    }

    expiration {
      days = 90
    }
  }
}

resource "aws_s3_bucket_notification" "reports" {
  bucket = aws_s3_bucket.reports.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.mailer.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "reports/"
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.mailer_from_s3]
}

# --- Queue: one entry point for the schedule, the Makefile and later the API ---------------------------

resource "aws_sqs_queue" "requests_dlq" {
  name                      = "${var.project_name}-report-requests-dlq"
  message_retention_seconds = 1209600
  max_message_size          = 1048576
}

resource "aws_sqs_queue" "requests" {
  name             = "${var.project_name}-report-requests"
  max_message_size = 1048576
  # 6 x the builder's 60 s timeout: a message must not become visible again while the first attempt runs.
  visibility_timeout_seconds = 360
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.requests_dlq.arn
    maxReceiveCount     = 3
  })
}

# --- Trigger 1: the schedule sends a command to the queue -----------------------------------------------

resource "aws_iam_role" "scheduler" {
  # Names CloudFormation generated; kept so the roles were imported, not re-created.
  name = "${var.project_name}-reports-SchedulerRole-oujUJe29ZbTR"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "send-report-request"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sqs:SendMessage"
      Resource = aws_sqs_queue.requests.arn
    }]
  })
}

resource "aws_scheduler_schedule" "weekly" {
  name                         = "${var.project_name}-weekly-report"
  description                  = "Ask for last week's meetings report every Monday morning."
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.report_timezone
  state                        = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sqs_queue.requests.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{\"week\": null, \"source\": \"schedule\"}"
  }
}

# --- Trigger 2: report-builder takes messages from the queue --------------------------------------------

resource "aws_cloudwatch_log_group" "builder" {
  name              = "/aws/lambda/${var.project_name}-report-builder"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "builder" {
  name               = "${var.project_name}-reports-BuilderRole-wIaBp44xtcOs"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "builder_logs" {
  role       = aws_iam_role.builder.name
  policy_arn = local.lambda_basic_execution
}

# Database sign-in, writing reports, and reading its queue (the Lambda service polls SQS for it).
resource "aws_iam_role_policy" "builder" {
  name = "build-report"
  role = aws_iam_role.builder.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "rds-db:connect"
        Resource = "arn:${local.partition}:rds-db:${var.region}:${local.account_id}:dbuser:${var.db_resource_id}/${var.db_username}"
      },
      {
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "arn:${local.partition}:s3:::${local.reports_bucket}/reports/*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.requests.arn
      },
    ]
  })
}

resource "aws_lambda_function" "builder" {
  function_name = "${var.project_name}-report-builder"
  description   = "Builds one week's meetings report and saves it to S3."
  package_type  = "Image"
  image_uri     = var.backend_image_uri
  architectures = [var.architecture]
  memory_size   = 512
  # Aurora may need ~15 s to resume from 0 ACU. The queue's visibility timeout is 6 x this.
  timeout = 60
  role    = aws_iam_role.builder.arn

  # Same image as the API, another handler.
  image_config {
    command = ["app.reports.lambda_handler.handler"]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.builder.name
  }

  environment {
    variables = {
      DB_HOST          = var.db_host
      DB_PORT          = "5432"
      DB_NAME          = var.db_name
      DB_USER          = var.db_username
      DB_IAM_AUTH      = "true"
      DB_NULL_POOL     = "true"
      REPORTS_BUCKET   = local.reports_bucket
      REPORT_TIMEZONE  = var.report_timezone
      REPORT_FAIL_WEEK = var.report_fail_week
    }
  }

  # CI deploys new images with `update-function-code` (Makefile: update_image_function).
  lifecycle {
    ignore_changes = [image_uri]
  }
}

resource "aws_lambda_event_source_mapping" "builder_queue" {
  event_source_arn = aws_sqs_queue.requests.arn
  function_name    = aws_lambda_function.builder.arn
  batch_size       = 1
  enabled          = true
}

# --- Trigger 3: a new file in reports/ starts report-mailer ---------------------------------------------

resource "aws_cloudwatch_log_group" "mailer" {
  name              = "/aws/lambda/${var.project_name}-report-mailer"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "mailer" {
  name               = "${var.project_name}-reports-MailerRole-kpcbEOl1e2RK"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "mailer_logs" {
  role       = aws_iam_role.mailer.name
  policy_arn = local.lambda_basic_execution
}

# Reads reports and sends email. No s3:PutObject: a function started by uploads to reports/ that
# uploaded to reports/ would start itself forever.
resource "aws_iam_role_policy" "mailer" {
  name = "mail-report"
  role = aws_iam_role.mailer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:${local.partition}:s3:::${local.reports_bucket}/reports/*"
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "arn:${local.partition}:ses:${var.region}:${local.account_id}:identity/*"
      },
    ]
  })
}

data "archive_file" "mailer" {
  type        = "zip"
  source_file = "${path.module}/lambda/report_mailer/index.py"
  output_path = "${path.module}/.terraform/report_mailer.zip"
}

resource "aws_lambda_function" "mailer" {
  function_name    = "${var.project_name}-report-mailer"
  description      = "Emails each new report in S3 through SES."
  runtime          = "python3.12"
  handler          = "index.handler"
  architectures    = ["arm64"]
  memory_size      = 128
  timeout          = 30
  role             = aws_iam_role.mailer.arn
  filename         = data.archive_file.mailer.output_path
  source_code_hash = data.archive_file.mailer.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.mailer.name
  }

  environment {
    variables = {
      SENDER     = "reports@${var.mail_domain}"
      RECIPIENTS = join(",", var.report_recipients)
    }
  }
}

resource "aws_lambda_permission" "mailer_from_s3" {
  statement_id   = "${var.project_name}-reports-MailerInvokePermission-ofglr4kYjYzo"
  function_name  = aws_lambda_function.mailer.function_name
  action         = "lambda:InvokeFunction"
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.reports.arn
  source_account = local.account_id
}

# --- SES: the sending domain (DKIM CNAMEs at the DNS provider) and, for the sandbox, the recipient ------

resource "aws_sesv2_email_identity" "domain" {
  email_identity = var.mail_domain
}

resource "aws_sesv2_email_identity" "recipient" {
  email_identity = var.report_recipients[0]
}
