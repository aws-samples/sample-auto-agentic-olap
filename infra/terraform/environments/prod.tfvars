environment                    = "prod"
region                         = "us-east-1"
vpc_cidr                       = "10.20.0.0/16"
az_count                       = 3
redshift_base_rpu              = 16
redshift_max_rpu               = 64
redshift_usage_limit_rpu_hours = 2000
service_desired_count          = 2

# Required. The deployment is HTTPS only, so all three must be set.
# The certificate must live in `region` above and cover app_domain.
#
# app_domain           = "medical.example.com"
# route53_zone_name    = "example.com"
# acm_certificate_arn  = "arn:aws:acm:us-east-1:<account>:certificate/<id>"

# service_image = "<account>.dkr.ecr.us-east-1.amazonaws.com/medical-data-service:<tag>"
