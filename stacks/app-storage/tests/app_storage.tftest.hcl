# `terraform test` with a mock provider: no AWS, no Moto. Checks the stack's own promises.
# The backend block is ignored by `terraform test`.

mock_provider "aws" {
  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:eu-west-1:111122223333:mock"
    }
  }
}

variables {
  environment = "dev"
}

run "names_and_tags_follow_the_convention" {
  command = plan

  assert {
    condition     = aws_s3_bucket.uploads.bucket == "code-clinic-dev-uploads"
    error_message = "bucket name must be <project>-<environment>-uploads"
  }

  assert {
    condition     = aws_sqs_queue.jobs.name == "code-clinic-dev-jobs"
    error_message = "queue name must be <project>-<environment>-jobs"
  }
}

run "bucket_is_private_versioned_and_encrypted" {
  command = apply

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.uploads.block_public_acls,
      aws_s3_bucket_public_access_block.uploads.block_public_policy,
      aws_s3_bucket_public_access_block.uploads.ignore_public_acls,
      aws_s3_bucket_public_access_block.uploads.restrict_public_buckets,
    ])
    error_message = "all four public access block settings must be on"
  }

  assert {
    condition     = one(aws_s3_bucket_versioning.uploads.versioning_configuration).status == "Enabled"
    error_message = "versioning must be enabled (a classic thing to 'temporarily' suspend in the console)"
  }
}

run "failed_jobs_go_to_the_dead_letter_queue" {
  command = apply

  assert {
    condition     = jsondecode(aws_sqs_queue.jobs.redrive_policy).maxReceiveCount == 5
    error_message = "jobs must move to the DLQ after 5 failed receives"
  }

  assert {
    condition     = jsondecode(aws_sqs_queue.jobs.redrive_policy).deadLetterTargetArn == aws_sqs_queue.jobs_dlq.arn
    error_message = "the redrive policy must point at the DLQ"
  }
}

run "rejects_unknown_environment" {
  command = plan

  variables {
    environment = "qa"
  }

  expect_failures = [var.environment]
}
