variable "region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type        = string
  description = "dev | staging | prod"
}

variable "name_prefix" {
  type    = string
  default = "medical"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of AZs for subnets. Redshift Serverless requires at least 3."
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 3
    error_message = "az_count must be at least 3: Redshift Serverless needs subnets in three Availability Zones."
  }
}

variable "redshift_database" {
  type    = string
  default = "medical_dw"
}

variable "redshift_admin_username" {
  type    = string
  default = "adminuser"
}

variable "redshift_base_rpu" {
  type    = number
  default = 8
}

variable "redshift_max_rpu" {
  type    = number
  default = 32
}

variable "redshift_usage_limit_rpu_hours" {
  type    = number
  default = 500
}

variable "service_image" {
  type        = string
  description = "ECR image URI for the FastAPI data service"
  default     = ""
}

variable "service_desired_count" {
  type    = number
  default = 2
}

// One hostname serves both the Admin UI and the API. Sharing an origin removes
// the CORS configuration and the mixed-content failure that appears when an
// HTTPS page calls a plaintext API.
variable "app_domain" {
  type        = string
  description = "Hostname for the Admin UI and API, e.g. medical.example.com"

  validation {
    condition     = length(trimspace(var.app_domain)) > 0
    error_message = "app_domain is required: the deployment is HTTPS only, so it needs a name the certificate covers."
  }
}

variable "route53_zone_name" {
  type        = string
  description = "Public hosted zone holding app_domain, e.g. example.com"

  validation {
    condition     = length(trimspace(var.route53_zone_name)) > 0
    error_message = "route53_zone_name is required to create the app_domain record."
  }
}

// Must live in the same region as the ALB. A certificate that does not cover
// app_domain will attach without complaint and then fail in the browser.
variable "acm_certificate_arn" {
  type        = string
  description = "ACM certificate ARN covering app_domain, in this region"

  validation {
    condition     = can(regex("^arn:aws:acm:", var.acm_certificate_arn))
    error_message = "acm_certificate_arn is required: plaintext HTTP is not served, so the HTTPS listener cannot be built without it."
  }
}

// Port 80 only ever answers with a 301 to HTTPS. Disable it to close the port.
variable "enable_http_redirect" {
  type        = bool
  description = "Open port 80 for an HTTPS redirect only"
  default     = true
}

# ---------------------------------------------------------------------------
# Cognito. The ALB validates tokens from this user pool (alb.tf): ID tokens
# from the portal client on /api, access tokens from the agent client on /mcp.
# The pool and app clients are created out-of-band (see
# medical-data-service/docs/mcp-oauth.md) and passed in as ids.
# ---------------------------------------------------------------------------

variable "cognito_user_pool_id" {
  type        = string
  description = "Existing Cognito user pool id (portal login + MCP OAuth)"
  validation {
    condition     = length(var.cognito_user_pool_id) > 0
    error_message = "Required: the ALB cannot validate tokens without it."
  }
}

variable "cognito_client_id" {
  type        = string
  description = "Cognito app client id for the portal (Authorization Code + PKCE)"
  validation {
    condition     = length(var.cognito_client_id) > 0
    error_message = "Required: the ALB cannot validate tokens without it."
  }
}

variable "cognito_agent_client_id" {
  type        = string
  description = "Cognito public app client id agents use for MCP OAuth (no DCR)"
  validation {
    condition     = length(var.cognito_agent_client_id) > 0
    error_message = "Required: the ALB cannot validate tokens without it."
  }
}

variable "cognito_domain" {
  type        = string
  description = "Cognito hosted-UI domain, e.g. <prefix>.auth.<region>.amazoncognito.com"
  validation {
    condition     = length(var.cognito_domain) > 0
    error_message = "Required: the ALB cannot validate tokens without it."
  }
}

variable "redshift_query_timeout" {
  type        = number
  description = "Seconds the service waits for a Redshift statement before cancelling. Must accommodate Serverless cold-start/first-compile (observed 200-500s) and be <= alb_idle_timeout."
  default     = 300
}

variable "alb_idle_timeout" {
  type        = number
  description = "ALB idle timeout (s). Must be >= redshift_query_timeout because MCP json_response sends no bytes until the query completes."
  default     = 300
}
