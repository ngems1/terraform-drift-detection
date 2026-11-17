plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  # renovate: datasource=github-releases depName=terraform-linters/tflint-ruleset-aws
  version = "0.50.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
