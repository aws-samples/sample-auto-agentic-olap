// One hostname for the Admin UI and the API, pointed at the ALB.
//
// An A record with an alias target is used rather than a CNAME: alias queries
// are not billed, and a zone apex cannot hold a CNAME at all.

data "aws_route53_zone" "main" {
  name         = var.route53_zone_name
  private_zone = false
}

resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.app_domain
  type    = "A"

  alias {
    name                   = aws_lb.service.dns_name
    zone_id                = aws_lb.service.zone_id
    evaluate_target_health = true
  }
}

// IPv6. Harmless when clients are v4-only, and it avoids a broken experience
// for anyone on a v6-only network.
resource "aws_route53_record" "app_ipv6" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.app_domain
  type    = "AAAA"

  alias {
    name                   = aws_lb.service.dns_name
    zone_id                = aws_lb.service.zone_id
    evaluate_target_health = true
  }
}
