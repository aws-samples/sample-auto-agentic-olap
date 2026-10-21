# --- ECS task execution role (pull image, write logs) ---

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.prefix}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --- Task role (application permissions: Redshift Data API, Secrets, logs) ---

resource "aws_iam_role" "task" {
  name               = "${local.prefix}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  # ExecuteStatement and GetCredentials support the workgroup resource type,
  # so they are scoped to this workgroup only.
  statement {
    sid = "RedshiftDataExecute"
    actions = [
      "redshift-data:ExecuteStatement",
      "redshift-serverless:GetCredentials",
    ]
    resources = [aws_redshiftserverless_workgroup.main.arn]
  }
  # Describe/GetStatementResult/Cancel have no resource type in the Service
  # Authorization Reference (resource must be "*"). The condition limits them to
  # statements this same role submitted.
  statement {
    sid = "RedshiftDataOwnStatements"
    actions = [
      "redshift-data:DescribeStatement",
      "redshift-data:GetStatementResult",
      "redshift-data:CancelStatement", # cancel timed-out statements so they do not pile up
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "redshift-data:statement-owner-iam-userid"
      values   = ["$${aws:userid}"]
    }
  }
  statement {
    sid       = "ReadRedshiftSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_redshiftserverless_namespace.main.admin_password_secret_arn]
  }
  statement {
    sid = "McpApiKeysSecret"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${var.mcp_api_keys_secret_name}-*"]
  }
  statement {
    sid       = "CognitoResolveEmail"
    actions   = ["cognito-idp:AdminGetUser"]
    resources = ["arn:aws:cognito-idp:${var.region}:${data.aws_caller_identity.current.account_id}:userpool/${var.cognito_user_pool_id}"]
  }
  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "${aws_cloudwatch_log_group.service.arn}:*",
      "${aws_cloudwatch_log_group.dbt.arn}:*",
    ]
  }
}

data "aws_caller_identity" "current" {}

resource "aws_iam_role_policy" "task" {
  name   = "${local.prefix}-task-policy"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}
