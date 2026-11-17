variable "project" {
  description = "Project name, used in resource names and tags"
  type        = string
  default     = "code-clinic"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "job_timeout_seconds" {
  description = "SQS visibility timeout: how long a worker has to finish a job"
  type        = number
  default     = 60
}

variable "aws_endpoint" {
  description = "LOCAL TESTING ONLY: an AWS emulator URL (e.g. http://localhost:5000). Leave null for real AWS."
  type        = string
  default     = null
}
