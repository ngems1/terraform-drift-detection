# S3 backend for app-storage/staging (terraform init -backend-config=backends/staging.hcl).
# The drift role only needs to READ this state; use_lockfile is irrelevant for -lock=false.
bucket       = "example-org-tfstate-staging"
key          = "app-storage/staging.tfstate"
region       = "eu-west-1"
encrypt      = true
use_lockfile = true
