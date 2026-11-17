output "bucket" {
  description = "Uploads bucket name"
  value       = aws_s3_bucket.uploads.id
}

output "queue_url" {
  description = "Jobs queue URL"
  value       = aws_sqs_queue.jobs.url
}
