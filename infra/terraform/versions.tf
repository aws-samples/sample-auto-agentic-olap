terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial configuration on purpose. A backend block cannot interpolate
  # variables, so hardcoding the key here would make every environment share one
  # state file, and applying prod would then rewrite or destroy dev.
  #
  # Values come from a per-environment file:
  #
  #   terraform init -backend-config=environments/dev.s3.tfbackend
  #
  # Switching environments requires an explicit re-init, which is the point: it
  # is hard to target the wrong environment by accident.
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "medical-olap"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
