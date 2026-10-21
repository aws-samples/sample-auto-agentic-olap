// One-off dbt runner.
//
// The warehouse is private, so dbt cannot run from a laptop. This defines a
// Fargate task that runs `dbt seed && dbt run && dbt test` from inside the
// private subnets, reusing the service security group (already allowed to reach
// Redshift on 5439). It is a task definition, not a service: it runs to
// completion and stops.

variable "dbt_runner_image" {
  type        = string
  description = "ECR image URI for the dbt runner"
  default     = ""
}

resource "aws_cloudwatch_log_group" "dbt" {
  name              = "/ecs/${local.prefix}-dbt"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "dbt" {
  count                    = var.dbt_runner_image == "" ? 0 : 1
  family                   = "${local.prefix}-dbt"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "dbt"
      image     = var.dbt_runner_image
      essential = true
      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "REDSHIFT_HOST", value = aws_redshiftserverless_workgroup.main.endpoint[0].address },
        { name = "REDSHIFT_SECRET_ARN", value = aws_redshiftserverless_namespace.main.admin_password_secret_arn },
        { name = "REDSHIFT_DATABASE", value = var.redshift_database },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.dbt.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "dbt"
        }
      }
    }
  ])
}

output "dbt_task_family" {
  value = var.dbt_runner_image == "" ? "" : aws_ecs_task_definition.dbt[0].family
}
