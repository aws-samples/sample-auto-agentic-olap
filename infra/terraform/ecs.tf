resource "aws_ecs_cluster" "main" {
  name = "${local.prefix}-cluster"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "service" {
  name              = "/ecs/${local.prefix}-service"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "service" {
  family                   = "${local.prefix}-service"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name         = "data-service"
      image        = var.service_image
      essential    = true
      portMappings = [{ containerPort = 8000 }]
      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "REDSHIFT_WORKGROUP", value = aws_redshiftserverless_workgroup.main.workgroup_name },
        { name = "REDSHIFT_DATABASE", value = var.redshift_database },
        { name = "REDSHIFT_HOST", value = aws_redshiftserverless_workgroup.main.endpoint[0].address },
        { name = "REDSHIFT_SECRET_ARN", value = aws_redshiftserverless_namespace.main.admin_password_secret_arn },
        { name = "QUERY_EXECUTION_ENABLED", value = "true" },
        # Redshift Serverless cold-start / first-time query compilation can take
        # minutes; keep this above that so the first run of a new query shape
        # is not cancelled prematurely. Must be <= ALB idle timeout.
        { name = "REDSHIFT_QUERY_TIMEOUT", value = tostring(var.redshift_query_timeout) },
        # Portal login (Cognito) + MCP OAuth. The pool/clients are created
        # out-of-band and passed in as ids.
        { name = "MCP_OAUTH_ENABLED", value = tostring(var.mcp_oauth_enabled) },
        { name = "MCP_RESOURCE_URL", value = "https://${var.app_domain}/mcp" },
        { name = "COGNITO_ISSUER", value = "https://cognito-idp.${var.region}.amazonaws.com/${var.cognito_user_pool_id}" },
        { name = "COGNITO_CLIENT_ID", value = var.cognito_client_id },
        { name = "MCP_AGENT_CLIENT_ID", value = var.cognito_agent_client_id },
        { name = "COGNITO_DOMAIN", value = var.cognito_domain },
        { name = "ADMIN_EMAIL", value = var.admin_email },
        { name = "MCP_API_KEYS_SECRET", value = var.mcp_api_keys_secret_name },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "service"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "service" {
  name            = "${local.prefix}-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.service.arn
  desired_count   = var.service_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.service.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.service.arn
    container_name   = "data-service"
    container_port   = 8000
  }

  # The service cannot register targets before the listener exists.
  depends_on = [aws_lb_listener.https]
}
