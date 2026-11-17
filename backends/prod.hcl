# S3 backend for app-storage/prod (terraform init -backend-config=backends/prod.hcl).
# The drift role only needs to READ this state; use_lockfile is irrelevant for -lock=false.
bucket       = "example-org-tfstate-prod"
key          = "app-storage/prod.tfstate"
region       = "eu-west-1"
encrypt      = true
use_lockfile = true
