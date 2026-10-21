# Bootstrap module: creates the remote state backend (S3 + DynamoDB lock).
# Run this ONCE per account before enabling the S3 backend in the main config.
# This module intentionally uses LOCAL state (it creates the remote state store).

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "medical-olap"
      Purpose   = "terraform-remote-state"
      ManagedBy = "terraform"
    }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "state_bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for Terraform state"
}

variable "lock_table_name" {
  type    = string
  default = "medical-tf-lock"
}

resource "aws_s3_bucket" "tfstate" {
  bucket = var.state_bucket_name
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tf_lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.tf_lock.name
}

# One file per environment. The key differs per environment on purpose: sharing a
# key makes `apply` in one environment rewrite the other's resources.
output "dev_backend_config" {
  description = "Write to ../environments/dev.s3.tfbackend"
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.tfstate.bucket}"
    key            = "medical/dev/terraform.tfstate"
    region         = "${var.region}"
    dynamodb_table = "${aws_dynamodb_table.tf_lock.name}"
    encrypt        = true
  EOT
}

output "prod_backend_config" {
  description = "Write to ../environments/prod.s3.tfbackend"
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.tfstate.bucket}"
    key            = "medical/prod/terraform.tfstate"
    region         = "${var.region}"
    dynamodb_table = "${aws_dynamodb_table.tf_lock.name}"
    encrypt        = true
  EOT
}
