# app-storage: the stack the drift pipeline watches. A small but real set of resources that
# people love to "just fix in the console": a bucket, its settings, a queue and its DLQ.
#
# The same code runs against real AWS (CI, OIDC role) and against a local Moto server
# (`aws_endpoint = "http://localhost:5000"`, see tests/e2e/run.sh). Never point the local
# settings at a real account.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
  # State lives in S3 (configured per environment with -backend-config, see examples/).
  # Drift detection compares this state with reality, so it needs read access to it.
  backend "s3" {}
}

locals {
  local_mode = var.aws_endpoint != null
  name       = "${var.project}-${var.environment}"
}

provider "aws" {
  region = var.region

  # Local emulator only: fake credentials, no account lookups, path-style S3 URLs.
  access_key                  = local.local_mode ? "test" : null
  secret_key                  = local.local_mode ? "test" : null
  skip_credentials_validation = local.local_mode
  skip_requesting_account_id  = local.local_mode
  skip_metadata_api_check     = local.local_mode
  s3_use_path_style           = local.local_mode

  endpoints {
    s3  = var.aws_endpoint
    sqs = var.aws_endpoint
    sts = var.aws_endpoint
  }

  default_tags {
    tags = {
      project     = var.project
      environment = var.environment
      managed-by  = "terraform"
      stack       = "app-storage"
    }
  }
}

resource "aws_s3_bucket" "uploads" {
  bucket = "${local.name}-uploads"
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${local.name}-jobs-dlq"
  message_retention_seconds = 1209600 # 14 days: time to investigate failed jobs
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "jobs" {
  name                       = "${local.name}-jobs"
  visibility_timeout_seconds = var.job_timeout_seconds
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 5
  })
}
