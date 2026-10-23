// Public entry point. HTTPS only: the certificate is required, and port 80
// exists solely to redirect. No listener ever serves content over plaintext.

resource "aws_lb" "service" {
  name               = "${local.prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true

  # MCP uses Streamable HTTP with json_response=true: no bytes flow to the
  # client until the Redshift query finishes. The default 60s idle timeout
  # would sever a slow (cold-compile) query mid-flight, so keep it >= the
  # app-side REDSHIFT_QUERY_TIMEOUT.
  idle_timeout = var.alb_idle_timeout
}

resource "aws_lb_target_group" "service" {
  name        = "${local.prefix}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.service.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service.arn
  }
}

// Authentication happens here, not in the application. The rules below send
// /mcp and /api through the ALB's managed jwt-validation action: the token's
// RS256 signature is checked against the Cognito JWKS, `iss` and `exp` are
// enforced, and the extra claims bind the token to the right app client.
// Anything that fails is rejected by the ALB and never reaches the service.
//
// The default action above still forwards everything else: the static portal,
// /health, and the OAuth discovery document under /.well-known/. None of
// those return data.
locals {
  cognito_issuer = "https://cognito-idp.${var.region}.amazonaws.com/${var.cognito_user_pool_id}"
  cognito_jwks   = "${local.cognito_issuer}/.well-known/jwks.json"
}

// The portal reads its Cognito hosted-UI settings before the user has logged
// in, so this one /api path stays open. It returns public client ids only.
resource "aws_lb_listener_rule" "api_config" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  condition {
    path_pattern {
      values = ["/api/config"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service.arn
  }
}

// Portal REST API: requires a Cognito ID token issued to the portal client.
resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20

  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }

  action {
    type = "jwt-validation"
    jwt_validation {
      issuer        = local.cognito_issuer
      jwks_endpoint = local.cognito_jwks
      additional_claim {
        name   = "token_use"
        format = "single-string"
        values = ["id"]
      }
      additional_claim {
        name   = "aud"
        format = "single-string"
        values = [var.cognito_client_id]
      }
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service.arn
  }
}

// MCP endpoint: requires a Cognito access token issued to the agent client.
// Cognito access tokens carry no `aud`; `client_id` is what binds them to
// this resource.
resource "aws_lb_listener_rule" "mcp" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 30

  condition {
    path_pattern {
      values = ["/mcp", "/mcp/*"]
    }
  }

  action {
    type = "jwt-validation"
    jwt_validation {
      issuer        = local.cognito_issuer
      jwks_endpoint = local.cognito_jwks
      additional_claim {
        name   = "token_use"
        format = "single-string"
        values = ["access"]
      }
      additional_claim {
        name   = "client_id"
        format = "single-string"
        values = [var.cognito_agent_client_id]
      }
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service.arn
  }
}

// Port 80 answers with a redirect and nothing else. Without it, anyone typing
// the hostname without a scheme gets a connection error rather than the app.
// Set enable_http_redirect = false to close port 80 entirely.
resource "aws_lb_listener" "http_redirect" {
  count             = var.enable_http_redirect ? 1 : 0
  load_balancer_arn = aws_lb.service.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

output "alb_dns_name" {
  value       = aws_lb.service.dns_name
  description = "Use the app_domain record instead; the certificate does not cover this name."
}

output "app_url" {
  value = "https://${var.app_domain}"
}
