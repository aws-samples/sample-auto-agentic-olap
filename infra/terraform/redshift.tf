# Private Redshift Serverless: no public access, reachable only from the service SG.

resource "aws_redshiftserverless_namespace" "main" {
  namespace_name        = local.prefix
  db_name               = var.redshift_database
  admin_username        = var.redshift_admin_username
  manage_admin_password = true
  log_exports           = ["userlog", "connectionlog", "useractivitylog"]
}

resource "aws_redshiftserverless_workgroup" "main" {
  namespace_name       = aws_redshiftserverless_namespace.main.namespace_name
  workgroup_name       = "${local.prefix}-wg"
  base_capacity        = var.redshift_base_rpu
  max_capacity         = var.redshift_max_rpu
  publicly_accessible  = false
  subnet_ids           = aws_subnet.private[*].id
  security_group_ids   = [aws_security_group.redshift.id]
  enhanced_vpc_routing = true
}

resource "aws_redshiftserverless_usage_limit" "compute" {
  resource_arn  = aws_redshiftserverless_workgroup.main.arn
  usage_type    = "serverless-compute"
  amount        = var.redshift_usage_limit_rpu_hours
  period        = "monthly"
  breach_action = "deactivate"
}

output "redshift_endpoint" {
  value = aws_redshiftserverless_workgroup.main.endpoint[0].address
}

output "redshift_secret_arn" {
  value = aws_redshiftserverless_namespace.main.admin_password_secret_arn
}
