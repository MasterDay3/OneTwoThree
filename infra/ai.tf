# Was infra/ai.yml (Lab 6): Paste an agenda. The agenda-parser Lambda (the backend image, another
# handler) takes pasted text on a public, Cognito-authenticated function URL, asks Claude on Bedrock
# for a structured agenda and returns JSON. Outside any VPC, and it never writes: the browser saves
# the checked agenda through the main API. Plus the $5 monthly budget alarm.

resource "aws_cloudwatch_log_group" "parser" {
  name              = "/aws/lambda/${var.project_name}-agenda-parser"
  retention_in_days = var.log_retention_days
}

# Logs and Bedrock, nothing else.
resource "aws_iam_role" "parser" {
  name               = "${var.project_name}-ai-ParserRole-AksogpwZljUn"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "parser_logs" {
  role       = aws_iam_role.parser.name
  policy_arn = local.lambda_basic_execution
}

resource "aws_iam_role_policy" "parser" {
  name = "call-claude"
  role = aws_iam_role.parser.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      # "*" as Lab 6 says; narrowing it is discussed in docs/adr/0006-llm-agenda-parsing.md.
      Effect   = "Allow"
      Action   = "bedrock-mantle:CreateInference"
      Resource = "*"
    }]
  })
}

resource "aws_lambda_function" "parser" {
  function_name = "${var.project_name}-agenda-parser"
  description   = "Parses pasted text into a suggested meeting agenda with Claude on Bedrock."
  package_type  = "Image"
  image_uri     = var.backend_image_uri
  architectures = [var.architecture]
  memory_size   = 512
  # A parse takes 2-8 s; the SDK's own timeout (25 s) fires first.
  timeout = 30
  role    = aws_iam_role.parser.arn

  image_config {
    command = ["app.ai.agenda_lambda.handler"]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.parser.name
  }

  environment {
    variables = {
      PARSER_MODEL         = var.parser_model
      COGNITO_REGION       = var.region
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
      COGNITO_CLIENT_ID    = aws_cognito_user_pool_client.web.id
    }
  }

  lifecycle {
    ignore_changes = [image_uri]
  }
}

# Public URL; the handler checks the Cognito ID token on every call and rejects the rest with 401.
resource "aws_lambda_function_url" "parser" {
  function_name      = aws_lambda_function.parser.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = var.app_origins
    allow_methods = ["POST"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 300
  }
}

# A public function URL needs both permissions.
resource "aws_lambda_permission" "parser_url" {
  statement_id           = "${var.project_name}-ai-ParserFunctionUrlPermission-HYcTgiPU8Edd"
  function_name          = aws_lambda_function.parser.function_name
  action                 = "lambda:InvokeFunctionUrl"
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "parser_invoke" {
  statement_id             = "${var.project_name}-ai-ParserFunctionInvokePermission-fkrKInefd1An"
  function_name            = aws_lambda_function.parser.function_name
  action                   = "lambda:InvokeFunction"
  principal                = "*"
  invoked_via_function_url = true
}

# The whole account's cost this month: an email when it passes the limit, and one when the forecast does.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project_name}-monthly"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = format("%.1f", var.budget_limit_usd)
  limit_unit   = "USD"

  notification {
    notification_type          = "ACTUAL"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    notification_type          = "FORECASTED"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_email]
  }
}
